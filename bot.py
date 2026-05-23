import os
import sqlite3
import requests
from datetime import datetime, date
from calendar import monthrange
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

DB_PATH = os.environ.get("DB_PATH", "budget.db")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
EXCHANGE_API_KEY = os.environ.get("EXCHANGE_API_KEY", "")  # exchangerate-api.com (free)

# Your Telegram usernames - set in .env
USER1 = os.environ.get("USER1", "tim")
USER2 = os.environ.get("USER2", "masha")

CATEGORIES = [
    "🛒 Продукты",
    "🍽 Рестораны",
    "🛵 Доставка",
    "☕️ Кофе",
    "💪 Спортзал",
    "🏥 Страховка",
    "🏠 Аренда/ЖКХ",
    "👗 Одежда",
    "📦 Онлайн-покупки",
    "🎁 Подарки",
    "📝 Другое",
]

WALLETS = [
    ("нз_брат",    "НЗ у брата",       "$",  6000.0),
    ("крипто_хол", "Крипто холодный",  "$",  3000.0),
    ("крипто_теп", "Крипто тёплый",    "$",  1500.0),
    ("оборот",     "В обороте",        "$",  4000.0),
    ("нал_usd",    "Наличка $",        "$",  0.0),
    ("нал_eur",    "Наличка €",        "€",  0.0),
]

FIXED_EXPENSES = [
    ("Аренда квартиры",  1280.0),
    ("Интернет",           26.0),
    ("Свет",              100.0),
    ("Вода",               30.0),
    ("Спортзал",           35.0),
]
FIXED_TOTAL = sum(a for _, a in FIXED_EXPENSES)  # 1471 EUR

INCOME_USD = 3700.0
SAVINGS_USD = 500.0  # auto-saved, not in budget

# Conversation states
(
    ADD_CATEGORY, ADD_AMOUNT, ADD_DESC,
    DEBT_WHO, DEBT_AMOUNT, DEBT_CURRENCY, DEBT_DESC,
    WALLET_CHOOSE, WALLET_AMOUNT, WALLET_OP,
) = range(10)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        username    TEXT,
        amount_eur  REAL,
        category    TEXT,
        description TEXT,
        date        TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS debts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user   TEXT,
        amount      REAL,
        currency    TEXT,
        description TEXT,
        date        TEXT,
        settled     INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS wallets (
        key         TEXT PRIMARY KEY,
        label       TEXT,
        currency    TEXT,
        amount      REAL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS exchange_cache (
        pair        TEXT PRIMARY KEY,
        rate        REAL,
        updated     TEXT
    )''')
    for key, label, cur, amount in WALLETS:
        c.execute("INSERT OR IGNORE INTO wallets VALUES (?,?,?,?)", (key, label, cur, amount))
    conn.commit()
    conn.close()


def get_db():
    return sqlite3.connect(DB_PATH)


def get_usd_to_eur() -> float:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT rate, updated FROM exchange_cache WHERE pair='USD_EUR'")
    row = c.fetchone()
    conn.close()
    if row:
        updated = datetime.fromisoformat(row[1])
        if (datetime.now() - updated).seconds < 3600:
            return row[0]
    try:
        if EXCHANGE_API_KEY:
            url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/pair/USD/EUR"
            r = requests.get(url, timeout=5).json()
            rate = r["conversion_rate"]
        else:
            url = "https://open.er-api.com/v6/latest/USD"
            r = requests.get(url, timeout=5).json()
            rate = r["rates"]["EUR"]
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO exchange_cache VALUES ('USD_EUR',?,?)",
                     (rate, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return rate
    except Exception:
        return 0.92


def usd_to_eur(usd: float) -> float:
    return round(usd * get_usd_to_eur(), 2)


def eur_to_usd(eur: float) -> float:
    rate = get_usd_to_eur()
    return round(eur / rate, 2) if rate else round(eur / 0.92, 2)


def get_username(update: Update) -> str:
    return (update.effective_user.username or update.effective_user.first_name or "unknown").lower()


def month_expenses_eur() -> float:
    today = date.today()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT SUM(amount_eur) FROM expenses WHERE date LIKE ?",
              (f"{today.year}-{today.month:02d}%",))
    row = c.fetchone()
    conn.close()
    return row[0] or 0.0


def month_by_category() -> dict:
    today = date.today()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT category, SUM(amount_eur) FROM expenses WHERE date LIKE ? GROUP BY category",
              (f"{today.year}-{today.month:02d}%",))
    rows = c.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def total_savings_usd() -> float:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT currency, amount FROM wallets")
    rows = c.fetchall()
    conn.close()
    total = 0.0
    for cur, amt in rows:
        total += amt if cur == "$" else eur_to_usd(amt)
    return round(total, 2)


# ── /start ──────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Привет! Я ваш семейный бюджет-бот.*\n\n"
        "📌 *Команды:*\n"
        "/add — добавить трату\n"
        "/overview — остаток + накопления\n"
        "/stats — статистика по категориям\n"
        "/savings — кошельки с накоплениями\n"
        "/wallet — пополнить / снять с кошелька\n"
        "/debt — записать долг\n"
        "/debts — список долгов\n"
        "/history — последние 10 трат\n"
        "/fixed — фиксированные расходы\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── /add ────────────────────────────────────────────────────
async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat:{cat}")] for cat in CATEGORIES]
    await update.message.reply_text(
        "📂 *Выбери категорию:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADD_CATEGORY


async def add_category_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["category"] = query.data.replace("cat:", "")
    await query.edit_message_text(
        f"✅ Категория: *{ctx.user_data['category']}*\n\n"
        "💶 Введи сумму в €\n_(или `150 USD` чтобы конвертировать автоматически)_",
        parse_mode="Markdown"
    )
    return ADD_AMOUNT


async def add_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split()
    try:
        amount_raw = float(parts[0].replace(",", "."))
        currency = parts[1].upper() if len(parts) > 1 else "EUR"
        if currency in ("USD", "$"):
            amount_eur = usd_to_eur(amount_raw)
            note = f" _(${amount_raw} → €{amount_eur} по курсу)_"
        else:
            amount_eur = amount_raw
            note = ""
        ctx.user_data["amount_eur"] = amount_eur
        ctx.user_data["amount_note"] = note
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Пример: `350` или `40 USD`", parse_mode="Markdown")
        return ADD_AMOUNT
    await update.message.reply_text(
        f"💶 Сумма: *€{amount_eur}*{note}\n\n📝 Комментарий _(или `-` пропустить)_",
        parse_mode="Markdown"
    )
    return ADD_DESC


async def add_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    if desc == "-":
        desc = ""
    username = get_username(update)
    conn = get_db()
    conn.execute(
        "INSERT INTO expenses (username, amount_eur, category, description, date) VALUES (?,?,?,?,?)",
        (username, ctx.user_data["amount_eur"], ctx.user_data["category"], desc, date.today().isoformat())
    )
    conn.commit()
    conn.close()
    spent = month_expenses_eur()
    left = usd_to_eur(INCOME_USD) - FIXED_TOTAL - spent
    await update.message.reply_text(
        f"✅ *Записано!*\n"
        f"👤 @{username}  |  {ctx.user_data['category']}\n"
        f"💶 €{ctx.user_data['amount_eur']}{ctx.user_data.get('amount_note','')}"
        + (f"\n📝 _{desc}_" if desc else "") +
        f"\n\n📊 Потрачено за месяц: *€{spent:.0f}*\n"
        f"{'🟢' if left > 0 else '🔴'} Остаток: *€{left:.0f}*",
        parse_mode="Markdown"
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END


# ── /overview ───────────────────────────────────────────────
async def overview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
        f"🏦 Все накопления: *${sav_usd:,.0f}* ≈ *€{usd_to_eur(sav_usd):,.0f}*",
        parse_mode="Markdown"
    )


# ── /stats ───────────────────────────────────────────────────
async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cats = month_by_category()
    today = date.today()
    if not cats:
        await update.message.reply_text("📭 Трат за этот месяц ещё нет.")
        return
    total = sum(cats.values())
    lines = [f"📊 *Статистика — {today.strftime('%B %Y')}*\n"]
    for cat, amt in sorted(cats.items(), key=lambda x: -x[1]):
        pct = amt / total * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        lines.append(f"{cat}\n`{bar}` {pct:.0f}%  €{amt:.0f}\n")
    lines.append(f"💶 *Итого: €{total:.0f}*")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /history ─────────────────────────────────────────────────
async def history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, amount_eur, category, description, date FROM expenses ORDER BY id DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📭 Записей пока нет.")
        return
    lines = ["📜 *Последние 10 трат:*\n"]
    for user, amt, cat, desc, dt in rows:
        lines.append(f"`{dt[5:]}` {cat} — *€{amt:.0f}*  @{user}" + (f"\n    _{desc}_" if desc else ""))
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /fixed ───────────────────────────────────────────────────
async def fixed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = ["🏠 *Фиксированные расходы:*\n"]
    for name, amt in FIXED_EXPENSES:
        lines.append(f"• {name}: *€{amt:.0f}*")
    lines.append(f"\n💶 *Итого: €{FIXED_TOTAL:.0f}/мес*")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /savings ─────────────────────────────────────────────────
async def savings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, label, currency, amount FROM wallets")
    rows = c.fetchall()
    conn.close()
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
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /wallet ──────────────────────────────────────────────────
async def wallet_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, label, currency, amount FROM wallets")
    rows = c.fetchall()
    conn.close()
    keyboard = [
        [InlineKeyboardButton(f"{label}  {cur}{amt:,.0f}", callback_data=f"wlt:{key}")]
        for key, label, cur, amt in rows
    ]
    await update.message.reply_text(
        "🏦 *Выбери кошелёк:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return WALLET_CHOOSE


async def wallet_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["wallet_key"] = query.data.replace("wlt:", "")
    keyboard = [[
        InlineKeyboardButton("➕ Пополнить", callback_data="wop:add"),
        InlineKeyboardButton("➖ Снять",     callback_data="wop:sub"),
    ], [
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
    return WALLET_AMOUNT


async def wallet_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введи число.")
        return WALLET_AMOUNT
    key = ctx.user_data["wallet_key"]
    op  = ctx.user_data["wallet_op"]
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT label, currency, amount FROM wallets WHERE key=?", (key,))
    label, cur, old = c.fetchone()
    new = old + amount if op == "add" else (old - amount if op == "sub" else amount)
    conn.execute("UPDATE wallets SET amount=? WHERE key=?", (new, key))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"✅ *{label}* обновлён\n{cur}{old:,.0f} → *{cur}{new:,.0f}*",
        parse_mode="Markdown"
    )
    ctx.user_data.clear()
    return ConversationHandler.END


# ── /debt ────────────────────────────────────────────────────
async def debt_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤝 *Кто взял деньги?* Введи имя или @username:", parse_mode="Markdown")
    return DEBT_WHO


async def debt_who(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["debt_who"] = update.message.text.strip()
    await update.message.reply_text("💰 Сколько?")
    return DEBT_AMOUNT


async def debt_amount_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["debt_amount"] = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введи число.")
        return DEBT_AMOUNT
    keyboard = [[
        InlineKeyboardButton("$ USD",      callback_data="dcur:USD"),
        InlineKeyboardButton("€ EUR",      callback_data="dcur:EUR"),
    ], [
        InlineKeyboardButton("💵 Нал $",   callback_data="dcur:CASH_USD"),
        InlineKeyboardButton("💶 Нал €",   callback_data="dcur:CASH_EUR"),
    ]]
    await update.message.reply_text("Валюта:", reply_markup=InlineKeyboardMarkup(keyboard))
    return DEBT_CURRENCY


async def debt_currency_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["debt_currency"] = query.data.replace("dcur:", "")
    await query.edit_message_text("📝 Комментарий (или `-` пропустить):")
    return DEBT_DESC


async def debt_desc_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    if desc == "-":
        desc = ""
    conn = get_db()
    conn.execute(
        "INSERT INTO debts (from_user, amount, currency, description, date) VALUES (?,?,?,?,?)",
        (ctx.user_data["debt_who"], ctx.user_data["debt_amount"],
         ctx.user_data["debt_currency"], desc, date.today().isoformat())
    )
    conn.commit()
    conn.close()
    sym = {"USD": "$", "EUR": "€", "CASH_USD": "$ нал", "CASH_EUR": "€ нал"}.get(ctx.user_data["debt_currency"], "")
    await update.message.reply_text(
        f"✅ Записано: {ctx.user_data['debt_who']} взял *{sym}{ctx.user_data['debt_amount']:,.0f}*"
        + (f"\n📝 _{desc}_" if desc else ""),
        parse_mode="Markdown"
    )
    ctx.user_data.clear()
    return ConversationHandler.END


# ── /debts ───────────────────────────────────────────────────
async def debts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, from_user, amount, currency, description, date FROM debts WHERE settled=0 ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("✅ Долгов нет!")
        return
    sym_map = {"USD": "$", "EUR": "€", "CASH_USD": "$ нал", "CASH_EUR": "€ нал"}
    lines = ["🤝 *Открытые долги:*\n"]
    for rid, who, amt, cur, desc, dt in rows:
        lines.append(f"#{rid} | {who} — *{sym_map.get(cur,'')}{amt:,.0f}*  `{dt[5:]}`"
                     + (f"\n    _{desc}_" if desc else ""))
    lines.append("\nЧтобы закрыть: /settle <номер>")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def settle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /settle <номер>")
        return
    try:
        debt_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ Пример: /settle 3")
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT from_user, amount, currency FROM debts WHERE id=? AND settled=0", (debt_id,))
    row = c.fetchone()
    if not row:
        await update.message.reply_text("❌ Долг не найден или уже закрыт.")
        conn.close()
        return
    conn.execute("UPDATE debts SET settled=1 WHERE id=?", (debt_id,))
    conn.commit()
    conn.close()
    sym = {"USD": "$", "EUR": "€", "CASH_USD": "$ нал", "CASH_EUR": "€ нал"}.get(row[2], "")
    await update.message.reply_text(f"✅ Долг #{debt_id} закрыт! {row[0]} вернул {sym}{row[1]:,.0f}")


# ── main ─────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ADD_CATEGORY: [CallbackQueryHandler(add_category_chosen, pattern="^cat:")],
            ADD_AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_amount)],
            ADD_DESC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("wallet", wallet_start)],
        states={
            WALLET_CHOOSE: [CallbackQueryHandler(wallet_chosen, pattern="^wlt:")],
            WALLET_OP:     [CallbackQueryHandler(wallet_op,     pattern="^wop:")],
            WALLET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("debt", debt_start)],
        states={
            DEBT_WHO:      [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_who)],
            DEBT_AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_amount_handler)],
            DEBT_CURRENCY: [CallbackQueryHandler(debt_currency_handler, pattern="^dcur:")],
            DEBT_DESC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_desc_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("help",     start))
    app.add_handler(CommandHandler("overview", overview))
    app.add_handler(CommandHandler("stats",    stats))
    app.add_handler(CommandHandler("history",  history))
    app.add_handler(CommandHandler("savings",  savings))
    app.add_handler(CommandHandler("fixed",    fixed))
    app.add_handler(CommandHandler("debts",    debts))
    app.add_handler(CommandHandler("settle",   settle))

    print("🤖 Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
