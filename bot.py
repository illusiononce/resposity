import asyncio
import logging
import sqlite3
import requests
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("NoryxHack")

# ══════════════════════════════════════════════════════════════════
#   КОНФИГУРАЦИЯ  —  заполни перед запуском!
# ══════════════════════════════════════════════════════════════════

BOT_TOKEN        = "8951682715:AAGE3jsAR7h699XV582Hl9ZIEReJd4Y-mqo"
CHANNEL_USERNAME = "@noryxhack"
CHANNEL_ID       = -1003928878729   # ID канала (см. README)
ADMINS           = ["illusiononce", "ANTIITAPCHIKo"]  # юзернеймы без @
BETA_LINK        = "https://t.me/+ueQeqop01DRiM2Ni"
DATABASE_PATH    = "noryxhack.db"
PROMO_DISCOUNT   = 8  # %

PLANS = {
    "30D":      {"days": 30,    "stars": 90,  "label": "30 дней"},
    "90D":      {"days": 90,    "stars": 180, "label": "90 дней"},
    "LIFETIME": {"days": 99999, "stars": 300, "label": "Навсегда"},
}

# ══════════════════════════════════════════════════════════════════
#   БАЗА ДАННЫХ
# ══════════════════════════════════════════════════════════════════

def db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    c = db()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id         INTEGER UNIQUE NOT NULL,
            username      TEXT,
            role          TEXT DEFAULT 'FREE',
            status        TEXT DEFAULT 'DEFOLT',
            sub_bought_at TEXT,
            sub_expires   TEXT,
            media_balance REAL DEFAULT 0.0,
            is_banned     INTEGER DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now'))
        )""")
    c.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT UNIQUE NOT NULL,
            owner_tg_id INTEGER,
            discount    INTEGER DEFAULT 8,
            created_at  TEXT DEFAULT (datetime('now'))
        )""")
    c.execute("""
        CREATE TABLE IF NOT EXISTS promo_activations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            code         TEXT NOT NULL,
            tg_id        INTEGER NOT NULL,
            activated_at TEXT DEFAULT (datetime('now'))
        )""")
    c.commit(); c.close()


# ── users ──────────────────────────────────────────────────────────

def get_user(tg_id):
    c = db()
    u = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    c.close(); return u

def get_user_by_username(username):
    c = db()
    u = c.execute("SELECT * FROM users WHERE username=?",
                  (username.lstrip("@"),)).fetchone()
    c.close(); return u

def get_all_users():
    c = db()
    rows = c.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    c.close(); return rows

def upsert_user(tg_id, username):
    c = db()
    c.execute("""
        INSERT INTO users (tg_id, username) VALUES (?,?)
        ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username
    """, (tg_id, username or ""))
    c.commit(); c.close()

def ban_user(username):
    c = db()
    c.execute("UPDATE users SET is_banned=1 WHERE username=?",
              (username.lstrip("@"),))
    c.commit(); c.close()

def unban_user(username):
    c = db()
    c.execute("UPDATE users SET is_banned=0 WHERE username=?",
              (username.lstrip("@"),))
    c.commit(); c.close()

def is_banned(tg_id):
    u = get_user(tg_id)
    return bool(u and u["is_banned"])

def has_active_sub(tg_id):
    u = get_user(tg_id)
    if not u or u["role"] == "FREE" or not u["sub_expires"]:
        return False
    return datetime.strptime(u["sub_expires"], "%Y-%m-%d %H:%M:%S") > datetime.now()

def grant_sub(tg_id, days):
    now = datetime.now()
    exp = now + timedelta(days=days)
    c = db()
    c.execute("""
        UPDATE users SET role='BETA', sub_bought_at=?, sub_expires=?
        WHERE tg_id=?
    """, (now.strftime("%Y-%m-%d %H:%M:%S"),
          exp.strftime("%Y-%m-%d %H:%M:%S"), tg_id))
    c.commit(); c.close()

# ── media ──────────────────────────────────────────────────────────

def set_media(tg_id):
    c = db()
    c.execute("UPDATE users SET status='MEDIA' WHERE tg_id=?", (tg_id,))
    c.commit(); c.close()

def get_media_users():
    c = db()
    rows = c.execute("SELECT * FROM users WHERE status='MEDIA'").fetchall()
    c.close(); return rows

def add_balance(tg_id, amount):
    c = db()
    c.execute("UPDATE users SET media_balance=media_balance+? WHERE tg_id=?",
              (amount, tg_id))
    c.commit(); c.close()

# ── promos ─────────────────────────────────────────────────────────

def get_all_promos():
    c = db()
    rows = c.execute("SELECT * FROM promo_codes ORDER BY created_at DESC").fetchall()
    c.close(); return rows

def get_activated_promos():
    c = db()
    rows = c.execute("""
        SELECT code, COUNT(*) as cnt FROM promo_activations
        GROUP BY code ORDER BY cnt DESC
    """).fetchall()
    c.close(); return rows

def create_promo(code, owner_id):
    c = db()
    try:
        c.execute("INSERT INTO promo_codes (code,owner_tg_id,discount) VALUES (?,?,?)",
                  (code, owner_id, PROMO_DISCOUNT))
        c.commit(); c.close(); return True
    except sqlite3.IntegrityError:
        c.close(); return False

def delete_promo(code):
    c = db()
    cur = c.execute("DELETE FROM promo_codes WHERE code=?", (code,))
    c.commit(); deleted = cur.rowcount > 0; c.close(); return deleted

def activate_promo(code, tg_id):
    c = db()
    promo = c.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
    if not promo:
        c.close(); return None
    already = c.execute(
        "SELECT id FROM promo_activations WHERE code=? AND tg_id=?",
        (code, tg_id)).fetchone()
    if already:
        c.close(); return -1
    c.execute("INSERT INTO promo_activations (code,tg_id) VALUES (?,?)", (code, tg_id))
    c.commit(); discount = promo["discount"]; c.close(); return discount

# ══════════════════════════════════════════════════════════════════
#   ХЕЛПЕРЫ
# ══════════════════════════════════════════════════════════════════

def fmt_dt(s):
    if not s: return "—"
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
    except Exception:
        return s

def role_fmt(r):   return "👑 BETA"    if r == "BETA"  else "🆓 FREE"
def status_fmt(s): return "🌟 MEDIA"   if s == "MEDIA" else "⚙️ DEFOLT"

def is_admin(username):
    return (username or "").lstrip("@").lower() in [a.lower() for a in ADMINS]

async def check_sub_channel(bot: Bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(CHANNEL_ID, user_id)
        return m.status not in ("left", "kicked", "banned")
    except Exception:
        return False

# ══════════════════════════════════════════════════════════════════
#   КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════════

def kb(*rows):
    """Быстрый конструктор InlineKeyboardMarkup из списка кортежей (text, data/url)."""
    buttons = []
    for row in rows:
        line = []
        for item in row:
            text, action = item
            if action.startswith("http"):
                line.append(InlineKeyboardButton(text=text, url=action))
            else:
                line.append(InlineKeyboardButton(text=text, callback_data=action))
        buttons.append(line)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def back(target="main"):
    return kb([("◀️ Назад", f"back_{target}")])

def cancel(target="admin_panel"):
    return kb([("❌ Отмена", f"back_{target}")])

def main_menu_kb(admin=False, has_sub=False):
    rows = [
        [("❔ Информация",         "info")],
        [("💸 Купить клиент",      "buy")],
        [("📱 Проверить подписку", "check_sub")],
        [("📝 Профиль",            "profile")],
    ]
    if has_sub:
        rows.append([("📁 Скачать Бета", "download_beta")])
    rows.append([("🪩 Медиа", "media_user")])
    if admin:
        rows.append([("🚩 Админ панель", "admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])

def subscribe_kb():
    return kb(
        [("📢 Подписаться на канал", f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
        [("✅ Я подписался",          "check_channel_sub")]
    )

def buy_kb():
    rows = []
    icons = {"30D": "🥉", "90D": "🥈", "LIFETIME": "👑"}
    for key, p in PLANS.items():
        rows.append([(f"{icons[key]} {key} — {p['stars']} ⭐", f"buy_plan_{key}")])
    rows.append([("◀️ Назад", "back_main")])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])

def confirm_buy_kb(plan_key, stars):
    return kb(
        [(f"💳 Оплатить {stars} ⭐",    f"pay_stars_{plan_key}_{stars}")],
        [("🏷 Использовать промокод",   f"use_promo_{plan_key}")],
        [("◀️ Назад",                   "buy")]
    )

def check_sub_kb(has_sub):
    if has_sub:
        return kb([("📁 Скачать Бета", "download_beta")], [("◀️ Назад", "back_main")])
    return kb([("💸 Купить клиент", "buy")], [("◀️ Назад", "back_main")])

def admin_panel_kb():
    return kb(
        [("🎟 Промокоды",   "admin_promos")],
        [("🎁 Выдать Бета",  "admin_give_beta")],
        [("👥 Юзеры",        "admin_users")],
        [("🌟 Медиа",        "admin_media")],
        [("◀️ Назад",        "back_main")]
    )

def admin_promos_kb():
    return kb(
        [("✅ Активированные", "admin_promos_activated")],
        [("📋 Все промокоды",  "admin_promos_all")],
        [("➕ Создать",         "admin_promo_create")],
        [("🗑 Удалить",         "admin_promo_delete")],
        [("◀️ Назад",          "admin_panel")]
    )

def give_beta_kb():
    icons = {"30D": "🥉", "90D": "🥈", "LIFETIME": "👑"}
    rows = [[(f"{icons[k]} {p['label']}", f"admin_give_{k}")] for k, p in PLANS.items()]
    rows.append([("◀️ Назад", "admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])

def admin_media_kb():
    return kb(
        [("🌟 Выдать медиа",     "admin_media_give")],
        [("💰 Начислить баланс", "admin_media_balance")],
        [("◀️ Назад",            "admin_panel")]
    )

def media_list_kb(users):
    rows = []
    for u in users:
        name = u["username"] or str(u["tg_id"])
        rows.append([(f"@{name}  💰 {u['media_balance']:.0f} ⭐",
                      f"admin_topup_{u['tg_id']}")])
    rows.append([("◀️ Назад", "admin_media")])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])

# ══════════════════════════════════════════════════════════════════
#   FSM СОСТОЯНИЯ
# ══════════════════════════════════════════════════════════════════

class BuyState(StatesGroup):
    waiting_promo = State()

class AdminState(StatesGroup):
    give_beta_user   = State()
    give_beta_plan   = State()
    promo_create_usr = State()
    promo_create_cod = State()
    promo_delete_cod = State()
    media_give_user  = State()
    topup_amount     = State()

# ══════════════════════════════════════════════════════════════════
#   РОУТЕР
# ══════════════════════════════════════════════════════════════════

router = Router()

# ── Отправить главное меню ─────────────────────────────────────────

async def send_main(bot: Bot, chat_id: int, tg_id: int, username: str):
    nick = f"@{username}" if username else f"#{tg_id}"
    text = (
        "╔══════════════════════╗\n"
        "║   🎮  NoryxHack Bot   ║\n"
        "╚══════════════════════╝\n\n"
        f"👋 Добро пожаловать, <b>{nick}</b>!\n\n"
        "Что сегодня хотите узнать?"
    )
    await bot.send_message(
        chat_id, text, parse_mode="HTML",
        reply_markup=main_menu_kb(
            admin=is_admin(username),
            has_sub=has_active_sub(tg_id)
        )
    )

# ── /start ────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(m: Message, bot: Bot):
    tg_id    = m.from_user.id
    username = m.from_user.username or ""
    upsert_user(tg_id, username)

    if is_banned(tg_id):
        await m.answer("🚫 <b>Вы заблокированы.</b>", parse_mode="HTML")
        return

    if not await check_sub_channel(bot, tg_id):
        await m.answer(
            "╔══════════════════════╗\n"
            "║   🎮  NoryxHack Bot   ║\n"
            "╚══════════════════════╝\n\n"
            "⚠️ Для использования бота необходимо\n"
            f"подписаться на канал <b>{CHANNEL_USERNAME}</b>!",
            parse_mode="HTML",
            reply_markup=subscribe_kb()
        )
        return

    await send_main(bot, m.chat.id, tg_id, username)

# ── Проверка подписки на канал ────────────────────────────────────

@router.callback_query(F.data == "check_channel_sub")
async def cb_check_channel(cb: CallbackQuery, bot: Bot):
    tg_id    = cb.from_user.id
    username = cb.from_user.username or ""
    upsert_user(tg_id, username)

    if is_banned(tg_id):
        await cb.answer("🚫 Вы заблокированы!", show_alert=True); return

    if not await check_sub_channel(bot, tg_id):
        await cb.answer("❌ Вы ещё не подписались!", show_alert=True); return

    await cb.message.delete()
    await send_main(bot, cb.message.chat.id, tg_id, username)
    await cb.answer()

# ── Назад в главное меню ──────────────────────────────────────────

@router.callback_query(F.data == "back_main")
async def cb_back_main(cb: CallbackQuery, bot: Bot, state: FSMContext):
    await state.clear()
    await cb.message.delete()
    await send_main(bot, cb.message.chat.id, cb.from_user.id, cb.from_user.username or "")
    await cb.answer()

# ── /ban  /unban ──────────────────────────────────────────────────

@router.message(Command("ban"))
async def cmd_ban(m: Message):
    if not is_admin(m.from_user.username or ""): return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование: /ban @username"); return
    ban_user(parts[1])
    await m.answer(f"🚫 @{parts[1].lstrip('@')} заблокирован.", parse_mode="HTML")

@router.message(Command("unban"))
async def cmd_unban(m: Message):
    if not is_admin(m.from_user.username or ""): return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование: /unban @username"); return
    unban_user(parts[1])
    await m.answer(f"✅ @{parts[1].lstrip('@')} разблокирован.", parse_mode="HTML")

# ══════════════════════════════════════════════════════════════════
#   ИНФОРМАЦИЯ
# ══════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "info")
async def cb_info(cb: CallbackQuery):
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   ❔  Информация      ║\n"
        "╚══════════════════════╝\n\n"
        "🎮 <b>NoryxHack</b> — клиент, разработанный тремя людьми:\n\n"
        "👤 <b>illusiononce</b>\n"
        "👤 <b>ANTIITAP</b>\n"
        "👤 <b>Blue_CatGG</b>\n\n"
        "📌 Изначально создан для сервера <b>SpookyTime</b>,\n"
        "но стал мультисерверным клиентом для комфортной\n"
        "и приятной игры.\n\n"
        "⚡ Наслаждайтесь лучшим игровым опытом!",
        parse_mode="HTML", reply_markup=back("main")
    )
    await cb.answer()

# ══════════════════════════════════════════════════════════════════
#   ПРОФИЛЬ
# ══════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "profile")
async def cb_profile(cb: CallbackQuery):
    tg_id    = cb.from_user.id
    username = cb.from_user.username or "—"
    u        = get_user(tg_id)
    if not u:
        await cb.answer("❌ Профиль не найден.", show_alert=True); return

    sub_ok = "✅ Активна" if has_active_sub(tg_id) else "❌ Не активна"
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   📝  Профиль         ║\n"
        "╚══════════════════════╝\n\n"
        f"👤 <b>Никнейм:</b> @{username}\n"
        f"🆔 <b>ID:</b> <code>{tg_id}</code>\n\n"
        f"📅 <b>Подписка куплена:</b>\n"
        f"    <code>{fmt_dt(u['sub_bought_at'])}</code>\n\n"
        f"⏳ <b>Подписка до:</b>\n"
        f"    <code>{fmt_dt(u['sub_expires'])}</code>\n\n"
        f"📊 <b>Статус подписки:</b> {sub_ok}\n\n"
        f"🎭 <b>Роль:</b>    {role_fmt(u['role'])}\n"
        f"🏷 <b>Статус:</b>  {status_fmt(u['status'])}",
        parse_mode="HTML", reply_markup=back("main")
    )
    await cb.answer()

# ══════════════════════════════════════════════════════════════════
#   ПРОВЕРИТЬ ПОДПИСКУ
# ══════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "check_sub")
async def cb_check_sub(cb: CallbackQuery):
    tg_id  = cb.from_user.id
    u      = get_user(tg_id)
    has_s  = has_active_sub(tg_id)
    if has_s:
        text = (
            "╔══════════════════════╗\n"
            "║  📱 Проверка подписки ║\n"
            "╚══════════════════════╝\n\n"
            "✅ <b>Подписка активна!</b>\n\n"
            f"⏳ Действует до: <code>{fmt_dt(u['sub_expires'])}</code>\n"
            f"🎭 Роль: {role_fmt(u['role'])}\n\n"
            "📁 Вы можете скачать клиент:"
        )
    else:
        text = (
            "╔══════════════════════╗\n"
            "║  📱 Проверка подписки ║\n"
            "╚══════════════════════╝\n\n"
            "❌ <b>Подписка не активна.</b>\n\n"
            "Приобретите клиент, чтобы получить доступ\n"
            "ко всем функциям <b>NoryxHack</b>!"
        )
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=check_sub_kb(has_s))
    await cb.answer()

# ══════════════════════════════════════════════════════════════════
#   СКАЧАТЬ БЕТУ
# ══════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "download_beta")
async def cb_download_beta(cb: CallbackQuery):
    if not has_active_sub(cb.from_user.id):
        await cb.answer("❌ У вас нет активной подписки!", show_alert=True); return
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   📁  Скачать Бета    ║\n"
        "╚══════════════════════╝\n\n"
        "✅ <b>Подписка подтверждена!</b>\n\n"
        "🚀 Нажмите кнопку ниже, чтобы перейти\n"
        "в закрытый канал с клиентом <b>NoryxHack</b>:",
        parse_mode="HTML",
        reply_markup=kb(
            [("📥 Перейти к загрузке", BETA_LINK)],
            [("◀️ Назад", "back_main")]
        )
    )
    await cb.answer()

# ══════════════════════════════════════════════════════════════════
#   МЕДИА (для медиа-пользователей)
# ══════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "media_user")
async def cb_media_user(cb: CallbackQuery):
    tg_id = cb.from_user.id
    u     = get_user(tg_id)
    if not u or u["status"] != "MEDIA":
        await cb.answer("🚫 У вас нет доступа к разделу Медиа.", show_alert=True); return
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   🪩  Медиа           ║\n"
        "╚══════════════════════╝\n\n"
        f"👤 <b>Пользователь:</b> @{u['username'] or tg_id}\n"
        f"🌟 <b>Статус:</b> {status_fmt(u['status'])}\n\n"
        f"💰 <b>На счету:</b> <code>{u['media_balance']:.0f} ⭐</code>\n\n"
        "📌 Баланс начисляется администратором\n"
        "за медиа-активность.",
        parse_mode="HTML", reply_markup=back("main")
    )
    await cb.answer()

# ══════════════════════════════════════════════════════════════════
#   ПОКУПКА — меню
# ══════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "buy")
async def cb_buy(cb: CallbackQuery):
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   💸  Купить клиент   ║\n"
        "╚══════════════════════╝\n\n"
        "🎮 Выберите тарифный план:\n\n"
        "🥉 <b>30D</b>       — 30 дней    <code>90 ⭐</code>\n"
        "🥈 <b>90D</b>       — 90 дней   <code>180 ⭐</code>\n"
        "👑 <b>LIFETIME</b> — навсегда  <code>300 ⭐</code>\n\n"
        "⭐ Оплата — звёздами Telegram.\n"
        "🏷 Можно применить промокод для скидки 8%.",
        parse_mode="HTML", reply_markup=buy_kb()
    )
    await cb.answer()

@router.callback_query(F.data.startswith("buy_plan_"))
async def cb_buy_plan(cb: CallbackQuery):
    key  = cb.data.split("buy_plan_")[1]
    plan = PLANS.get(key)
    if not plan:
        await cb.answer("❌ Тариф не найден.", show_alert=True); return
    icons = {"30D": "🥉", "90D": "🥈", "LIFETIME": "👑"}
    await cb.message.edit_text(
        f"╔══════════════════════╗\n"
        f"║   💳  Оплата          ║\n"
        f"╚══════════════════════╝\n\n"
        f"📦 <b>Тариф:</b> {icons.get(key,'')} {key} — {plan['label']}\n"
        f"💰 <b>Стоимость:</b> <code>{plan['stars']} ⭐</code>\n\n"
        f"🏷 Есть промокод? Нажмите кнопку ниже\n"
        f"для получения скидки <b>8%</b>.\n\n"
        f"✅ Доступ будет выдан автоматически.",
        parse_mode="HTML", reply_markup=confirm_buy_kb(key, plan["stars"])
    )
    await cb.answer()

# ── Промокод при покупке ──────────────────────────────────────────

@router.callback_query(F.data.startswith("use_promo_"))
async def cb_use_promo(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split("use_promo_")[1]
    await state.set_state(BuyState.waiting_promo)
    await state.update_data(plan_key=key)
    await cb.message.edit_text(
        "🏷 <b>Введите промокод:</b>\n\nОтправьте код следующим сообщением.",
        parse_mode="HTML", reply_markup=cancel("buy")
    )
    await cb.answer()

@router.message(BuyState.waiting_promo)
async def process_promo(m: Message, state: FSMContext):
    code = m.text.strip()
    data = await state.get_data()
    key  = data.get("plan_key", "30D")
    tg_id = m.from_user.id

    result = activate_promo(code, tg_id)
    await state.clear()

    if result is None:
        await m.answer("❌ <b>Промокод не найден!</b>", parse_mode="HTML"); return
    if result == -1:
        await m.answer("⚠️ <b>Вы уже использовали этот промокод.</b>", parse_mode="HTML"); return

    stars = PLANS[key]["stars"]
    discounted = int(stars * (1 - result / 100))
    await m.answer(
        f"✅ <b>Промокод применён!</b>\n\n"
        f"📦 Тариф: <b>{key}</b>\n"
        f"💰 Было: <s>{stars} ⭐</s>\n"
        f"🎉 Стало: <code>{discounted} ⭐</code> (скидка {result}%)\n\n"
        "Нажмите кнопку для оплаты:",
        parse_mode="HTML",
        reply_markup=kb(
            [(f"💳 Оплатить {discounted} ⭐", f"pay_stars_{key}_{discounted}")],
            [("◀️ Назад", "buy")]
        )
    )

# ── Инвойс (Stars) ────────────────────────────────────────────────

async def send_invoice(bot: Bot, chat_id: int, plan_key: str, stars: int):
    plan = PLANS[plan_key]
    await bot.send_invoice(
        chat_id=chat_id,
        title=f"NoryxHack — {plan_key}",
        description=(
            f"🎮 Доступ к клиенту NoryxHack\n"
            f"📅 Период: {plan['label']}\n"
            f"✅ Доступ выдаётся автоматически."
        ),
        payload=f"sub_{plan_key}_{chat_id}",
        currency="XTR",
        prices=[LabeledPrice(label=f"NoryxHack {plan_key}", amount=stars)],
        provider_token="",
    )

@router.callback_query(F.data.startswith("pay_stars_"))
async def cb_pay_stars(cb: CallbackQuery, bot: Bot):
    parts = cb.data.split("_")  # pay_stars_PLAN_AMOUNT
    plan_key = parts[2]
    stars    = int(parts[3])
    await cb.message.delete()
    await send_invoice(bot, cb.message.chat.id, plan_key, stars)
    await cb.answer()

@router.pre_checkout_query()
async def pre_checkout(pcq: PreCheckoutQuery):
    await pcq.answer(ok=True)

@router.message(F.successful_payment)
async def on_payment(m: Message, bot: Bot):
    payload = m.successful_payment.invoice_payload  # sub_PLAN_chatid
    parts   = payload.split("_")
    if len(parts) >= 2:
        plan_key = parts[1]
        plan     = PLANS.get(plan_key)
        if plan:
            grant_sub(m.from_user.id, plan["days"])
            nick = f"@{m.from_user.username}" if m.from_user.username else f"#{m.from_user.id}"
            await m.answer(
                "╔══════════════════════╗\n"
                "║  ✅  Оплата прошла!   ║\n"
                "╚══════════════════════╝\n\n"
                f"🎉 <b>Поздравляем, {nick}!</b>\n\n"
                f"📦 Тариф: <b>{plan_key}</b> — {plan['label']}\n"
                f"💰 Оплачено: <code>{m.successful_payment.total_amount} ⭐</code>\n\n"
                "📁 Теперь вам доступна кнопка <b>«Скачать Бета»</b>\n"
                "в главном меню!\n\n"
                "🚀 Приятной игры с NoryxHack!",
                parse_mode="HTML"
            )

# ══════════════════════════════════════════════════════════════════
#   АДМИН — главная панель
# ══════════════════════════════════════════════════════════════════

def admin_guard(cb: CallbackQuery) -> bool:
    if not is_admin(cb.from_user.username or ""):
        return False
    return True

@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(cb: CallbackQuery):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   🚩  Админ панель    ║\n"
        "╚══════════════════════╝\n\n"
        "👋 Добро пожаловать в панель управления!\n\n"
        "Выберите раздел:",
        parse_mode="HTML", reply_markup=admin_panel_kb()
    )
    await cb.answer()

@router.callback_query(F.data == "back_admin_panel")
async def cb_back_admin(cb: CallbackQuery, state: FSMContext):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    await state.clear()
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   🚩  Админ панель    ║\n"
        "╚══════════════════════╝\n\n"
        "Выберите раздел:",
        parse_mode="HTML", reply_markup=admin_panel_kb()
    )
    await cb.answer()

# ── Юзеры ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_users")
async def cb_admin_users(cb: CallbackQuery):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    users = get_all_users()
    if not users:
        text = "👥 Пользователей нет."
    else:
        lines = [
            "╔══════════════════════╗\n"
            "║   👥  Все юзеры       ║\n"
            "╚══════════════════════╝\n"
        ]
        for u in users[:50]:
            uname = f"@{u['username']}" if u["username"] else f"#{u['tg_id']}"
            sub   = "✅" if u["role"] == "BETA" else "❌"
            ban   = " 🚫" if u["is_banned"] else ""
            lines.append(
                f"{uname}{ban}\n"
                f"  {role_fmt(u['role'])}  {status_fmt(u['status'])}  📱{sub}\n"
            )
        text = "\n".join(lines)
        if len(users) > 50:
            text += f"\n<i>...и ещё {len(users)-50}. Всего: {len(users)}</i>"
    await cb.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=kb([("◀️ Назад", "admin_panel")])
    )
    await cb.answer()

# ── Выдать Бету ───────────────────────────────────────────────────

@router.callback_query(F.data == "admin_give_beta")
async def cb_admin_give_beta(cb: CallbackQuery, state: FSMContext):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    await state.set_state(AdminState.give_beta_user)
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   🎁  Выдать Бета     ║\n"
        "╚══════════════════════╝\n\n"
        "👤 Введите <b>юзернейм</b> пользователя\n"
        "(без @, например: <code>username</code>):",
        parse_mode="HTML", reply_markup=cancel("admin_panel")
    )
    await cb.answer()

@router.message(AdminState.give_beta_user)
async def admin_give_user(m: Message, state: FSMContext):
    if not is_admin(m.from_user.username or ""): return
    username = m.text.strip().lstrip("@")
    user     = get_user_by_username(username)
    if not user:
        await m.answer(
            f"❌ <b>@{username}</b> не найден. Убедитесь, что он запускал бота.",
            parse_mode="HTML", reply_markup=cancel("admin_panel")
        ); return
    await state.update_data(target_id=user["tg_id"], target_name=username)
    await state.set_state(AdminState.give_beta_plan)
    await m.answer(
        f"✅ Пользователь: <b>@{username}</b>\n\nВыберите период:",
        parse_mode="HTML", reply_markup=give_beta_kb()
    )

@router.callback_query(F.data.startswith("admin_give_"), AdminState.give_beta_plan)
async def admin_give_plan(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    key  = cb.data.split("admin_give_")[1]
    plan = PLANS.get(key)
    data = await state.get_data()
    tid  = data.get("target_id")
    name = data.get("target_name")
    await state.clear()
    if plan and tid:
        grant_sub(tid, plan["days"])
        try:
            await bot.send_message(tid,
                f"🎉 <b>Вам выдана подписка NoryxHack!</b>\n\n"
                f"📦 Тариф: <b>{key}</b> — {plan['label']}\n"
                f"📁 Главное меню → <b>Скачать Бета</b>",
                parse_mode="HTML")
        except Exception: pass
        await cb.message.edit_text(
            f"✅ Подписка <b>{key}</b> ({plan['label']}) выдана @{name}!",
            parse_mode="HTML", reply_markup=back("admin_panel")
        )
    await cb.answer()

# ── Промокоды (администратор) ─────────────────────────────────────

@router.callback_query(F.data == "admin_promos")
async def cb_admin_promos(cb: CallbackQuery):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   🎟  Промокоды       ║\n"
        "╚══════════════════════╝\n\n"
        "Управление промокодами:",
        parse_mode="HTML", reply_markup=admin_promos_kb()
    )
    await cb.answer()

@router.callback_query(F.data == "admin_promos_all")
async def cb_promos_all(cb: CallbackQuery):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    promos = get_all_promos()
    if not promos:
        text = "📋 Промокодов ещё нет."
    else:
        lines = [
            "╔══════════════════════╗\n"
            "║   📋  Все промокоды   ║\n"
            "╚══════════════════════╝\n"
        ]
        for p in promos:
            owner = get_user(p["owner_tg_id"]) if p["owner_tg_id"] else None
            oname = f"@{owner['username']}" if owner and owner["username"] else f"#{p['owner_tg_id']}"
            lines.append(
                f"🏷 <code>{p['code']}</code>\n"
                f"  👤 {oname}  💰 {p['discount']}%  📅 {fmt_dt(p['created_at'])}\n"
            )
        text = "\n".join(lines)
    await cb.message.edit_text(text, parse_mode="HTML",
        reply_markup=kb([("◀️ Назад", "admin_promos")]))
    await cb.answer()

@router.callback_query(F.data == "admin_promos_activated")
async def cb_promos_activated(cb: CallbackQuery):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    rows = get_activated_promos()
    if not rows:
        text = "✅ Ни один промокод ещё не активирован."
    else:
        lines = [
            "╔══════════════════════╗\n"
            "║ ✅ Активированные    ║\n"
            "╚══════════════════════╝\n"
        ]
        for r in rows:
            lines.append(f"🏷 <code>{r['code']}</code>  —  <b>{r['cnt']}x</b>")
        text = "\n".join(lines)
    await cb.message.edit_text(text, parse_mode="HTML",
        reply_markup=kb([("◀️ Назад", "admin_promos")]))
    await cb.answer()

@router.callback_query(F.data == "admin_promo_create")
async def cb_promo_create(cb: CallbackQuery, state: FSMContext):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    await state.set_state(AdminState.promo_create_usr)
    await cb.message.edit_text(
        "➕ <b>Создание промокода</b>\n\n"
        "👤 Введите юзернейм владельца промокода\n(без @):",
        parse_mode="HTML", reply_markup=cancel("admin_promos")
    )
    await cb.answer()

@router.message(AdminState.promo_create_usr)
async def promo_create_usr(m: Message, state: FSMContext):
    if not is_admin(m.from_user.username or ""): return
    username = m.text.strip().lstrip("@")
    user     = get_user_by_username(username)
    if not user:
        await m.answer(f"❌ <b>@{username}</b> не найден.", parse_mode="HTML",
                       reply_markup=cancel("admin_promos")); return
    await state.update_data(owner_id=user["tg_id"], owner_name=username)
    await state.set_state(AdminState.promo_create_cod)
    await m.answer(
        f"✅ Владелец: <b>@{username}</b>\n\n"
        "🏷 Введите название промокода\n(например: <code>HACK2024</code>):",
        parse_mode="HTML", reply_markup=cancel("admin_promos")
    )

@router.message(AdminState.promo_create_cod)
async def promo_create_cod(m: Message, state: FSMContext):
    if not is_admin(m.from_user.username or ""): return
    code = m.text.strip().upper()
    data = await state.get_data()
    await state.clear()
    ok = create_promo(code, data["owner_id"])
    text = (
        f"✅ Промокод <code>{code}</code> создан!\n"
        f"👤 @{data['owner_name']}  💰 {PROMO_DISCOUNT}%"
    ) if ok else f"❌ Промокод <code>{code}</code> уже существует!"
    await m.answer(text, parse_mode="HTML",
                   reply_markup=kb([("◀️ Назад", "admin_promos")]))

@router.callback_query(F.data == "admin_promo_delete")
async def cb_promo_delete(cb: CallbackQuery, state: FSMContext):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    promos = get_all_promos()
    if not promos:
        await cb.answer("📋 Промокодов нет.", show_alert=True); return
    await state.set_state(AdminState.promo_delete_cod)
    lines = ["🗑 <b>Удаление промокода</b>\n\nСуществующие коды:"]
    for p in promos:
        lines.append(f"  • <code>{p['code']}</code>")
    lines.append("\n✏️ Введите код для удаления:")
    await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                               reply_markup=cancel("admin_promos"))
    await cb.answer()

@router.message(AdminState.promo_delete_cod)
async def promo_delete_cod(m: Message, state: FSMContext):
    if not is_admin(m.from_user.username or ""): return
    code = m.text.strip().upper()
    await state.clear()
    ok = delete_promo(code)
    text = (f"✅ Промокод <code>{code}</code> удалён."
            if ok else f"❌ Промокод <code>{code}</code> не найден.")
    await m.answer(text, parse_mode="HTML",
                   reply_markup=kb([("◀️ Назад", "admin_promos")]))

# ── Медиа (администратор) ─────────────────────────────────────────

@router.callback_query(F.data == "admin_media")
async def cb_admin_media(cb: CallbackQuery):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║   🌟  Медиа           ║\n"
        "╚══════════════════════╝\n\n"
        "Управление медиа-пользователями:",
        parse_mode="HTML", reply_markup=admin_media_kb()
    )
    await cb.answer()

@router.callback_query(F.data == "admin_media_give")
async def cb_media_give(cb: CallbackQuery, state: FSMContext):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    await state.set_state(AdminState.media_give_user)
    await cb.message.edit_text(
        "🌟 <b>Выдать медиа</b>\n\n👤 Введите юзернейм (без @):",
        parse_mode="HTML", reply_markup=cancel("admin_media")
    )
    await cb.answer()

@router.message(AdminState.media_give_user)
async def media_give_user(m: Message, state: FSMContext, bot: Bot):
    if not is_admin(m.from_user.username or ""): return
    username = m.text.strip().lstrip("@")
    user     = get_user_by_username(username)
    if not user:
        await m.answer(f"❌ <b>@{username}</b> не найден.", parse_mode="HTML",
                       reply_markup=cancel("admin_media")); return
    await state.clear()
    set_media(user["tg_id"])
    try:
        await bot.send_message(user["tg_id"],
            "🌟 <b>Вам выдан статус MEDIA!</b>\n\n"
            "Теперь у вас есть доступ к разделу 🪩 Медиа.",
            parse_mode="HTML")
    except Exception: pass
    await m.answer(
        f"✅ Статус <b>MEDIA</b> выдан пользователю <b>@{username}</b>!",
        parse_mode="HTML", reply_markup=kb([("◀️ Назад", "admin_media")])
    )

@router.callback_query(F.data == "admin_media_balance")
async def cb_media_balance(cb: CallbackQuery):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    users = get_media_users()
    if not users:
        await cb.answer("📋 Медиа-пользователей нет.", show_alert=True); return
    await cb.message.edit_text(
        "╔══════════════════════╗\n"
        "║  💰  Начислить баланс ║\n"
        "╚══════════════════════╝\n\n"
        "Выберите пользователя:",
        parse_mode="HTML", reply_markup=media_list_kb(users)
    )
    await cb.answer()

@router.callback_query(F.data.startswith("admin_topup_"))
async def cb_admin_topup(cb: CallbackQuery, state: FSMContext):
    if not admin_guard(cb):
        await cb.answer("🚫 Нет доступа!", show_alert=True); return
    tid    = int(cb.data.split("admin_topup_")[1])
    target = get_user(tid)
    uname  = target["username"] if target else str(tid)
    bal    = target["media_balance"] if target else 0
    await state.set_state(AdminState.topup_amount)
    await state.update_data(topup_id=tid, topup_name=uname)
    await cb.message.edit_text(
        f"💰 <b>Начисление баланса</b>\n\n"
        f"👤 Пользователь: <b>@{uname}</b>\n"
        f"💎 Текущий баланс: <code>{bal:.0f} ⭐</code>\n\n"
        "Введите сумму для начисления (например: <code>13</code>):",
        parse_mode="HTML", reply_markup=cancel("admin_media_balance")
    )
    await cb.answer()

@router.message(AdminState.topup_amount)
async def topup_amount(m: Message, state: FSMContext, bot: Bot):
    if not is_admin(m.from_user.username or ""): return
    try:
        amount = float(m.text.strip())
        if amount <= 0: raise ValueError
    except ValueError:
        await m.answer("❌ Введите корректную сумму (число > 0).",
                       reply_markup=cancel("admin_media_balance")); return
    data  = await state.get_data()
    tid   = data["topup_id"]
    uname = data["topup_name"]
    await state.clear()
    add_balance(tid, amount)
    updated = get_user(tid)
    try:
        await bot.send_message(tid,
            f"💰 <b>Вам начислено {amount:.0f} ⭐ на медиа-баланс!</b>\n\n"
            f"💎 Новый баланс: <code>{updated['media_balance']:.0f} ⭐</code>",
            parse_mode="HTML")
    except Exception: pass
    await m.answer(
        f"✅ Начислено <b>{amount:.0f} ⭐</b> пользователю <b>@{uname}</b>\n"
        f"💎 Новый баланс: <code>{updated['media_balance']:.0f} ⭐</code>",
        parse_mode="HTML",
        reply_markup=kb([("◀️ Назад", "admin_media")])
    )

@router.callback_query(F.data == "back_admin_media_balance")
async def cb_back_media_bal(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb_media_balance(cb)

# ══════════════════════════════════════════════════════════════════
#   ЗАПУСК
# ══════════════════════════════════════════════════════════════════

async def main():
    init_db()
    logger.info("✅ База данных инициализирована")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("🚀 NoryxHack Bot запущен!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("🛑 Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
