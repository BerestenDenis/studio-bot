import asyncio
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from dotenv import load_dotenv
import os

BASE = Path(__file__).parent
load_dotenv(BASE.parent / ".env")
TOKEN = os.getenv("BOT_TOKEN", "").strip()
PROXY = os.getenv("PROXY_URL", "").strip()
ADMIN_FILE = BASE / "admin_id.txt"
DB_PATH = BASE / "studio.db"

STUDIO = {
    "name": "Фотостудия «Свет»",
    "address": "г. Москва, ул. Примерная, 12",
    "phone": "+7 900 000-00-00",
    "hours": "09:00–22:00",
}

DURATIONS = {"1": "1 час", "2": "2 часа", "3": "3 часа"}
PURPOSES = ["Портрет", "Семейная", "Бизнес", "Контент", "Другое"]


# ---------- база ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS slots(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL, time TEXT NOT NULL,
                busy INTEGER NOT NULL DEFAULT 0,
                UNIQUE(day, time));
            CREATE TABLE IF NOT EXISTS equipment(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS bookings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, name TEXT, phone TEXT,
                day TEXT, time TEXT, duration TEXT, purpose TEXT,
                equipment TEXT, status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            """
        )
        cur = conn.execute("SELECT COUNT(*) c FROM equipment")
        if cur.fetchone()["c"] == 0:
            for e in ("Постоянный свет", "Вспышки (студийные)", "Фоновые стойки"):
                conn.execute("INSERT INTO equipment(name) VALUES (?)", (e,))
        cur = conn.execute("SELECT COUNT(*) c FROM slots")
        if cur.fetchone()["c"] == 0:
            today = date.today()
            for d in range(1, 6):
                day = (today + timedelta(days=d)).isoformat()
                for t in ("10:00", "12:00", "14:00", "16:00", "18:00"):
                    conn.execute(
                        "INSERT OR IGNORE INTO slots(day,time) VALUES (?,?)", (day, t)
                    )


def free_slots(day=None):
    with db() as conn:
        if day:
            return conn.execute(
                "SELECT * FROM slots WHERE day=? AND busy=0 ORDER BY time", (day,)
            ).fetchall()
        return conn.execute(
            "SELECT DISTINCT day FROM slots WHERE busy=0 ORDER BY day LIMIT 7"
        ).fetchall()


def active_equipment():
    with db() as conn:
        return conn.execute(
            "SELECT * FROM equipment WHERE active=1 ORDER BY id"
        ).fetchall()


def admin_id():
    try:
        if ADMIN_FILE.exists():
            raw = ADMIN_FILE.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                return int(raw)
    except OSError:
        pass
    env = os.getenv("ADMIN_ID", "").strip()
    return int(env) if env.isdigit() else None


# ---------- клавиатуры ----------
def kb_days():
    rows, row = [], []
    for s in free_slots():
        d = date.fromisoformat(s["day"])
        row.append(InlineKeyboardButton(
            text=d.strftime("%d.%m"), callback_data=f"day:{s['day']}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_times(day):
    rows, row = [], []
    for s in free_slots(day):
        row.append(InlineKeyboardButton(
            text=s["time"], callback_data=f"time:{s['id']}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="back:days")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_durations():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=v, callback_data=f"dur:{k}") for k, v in DURATIONS.items()
    ]])


def kb_purposes():
    rows, row = [], []
    for i, p in enumerate(PURPOSES):
        row.append(InlineKeyboardButton(text=p, callback_data=f"pur:{i}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_equipment(selected):
    rows = []
    for e in active_equipment():
        mark = "✅ " if str(e["id"]) in selected else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{e['name']}", callback_data=f"eq:{e['id']}")])
    rows.append([InlineKeyboardButton(text="Готово →", callback_data="eq:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin_booking(bid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"ok:{bid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no:{bid}"),
    ]])


def kb_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Слоты", callback_data="adm:slots")],
        [InlineKeyboardButton(text="🎛 Оборудование", callback_data="adm:eq")],
        [InlineKeyboardButton(text="📋 Заявки", callback_data="adm:bookings")],
    ])


# ---------- состояния ----------
class St(StatesGroup):
    name = State()
    phone = State()
    slot_day = State()
    slot_time = State()
    eq_name = State()


router = Router()


@router.message(Command("start"))
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        f"Здравствуйте! Это бот бронирования {STUDIO['name']}.\n"
        "Выберите дату съёмки:",
        reply_markup=kb_days(),
    )


@router.callback_query(F.data.startswith("day:"))
async def pick_day(c: CallbackQuery):
    day = c.data.split(":")[1]
    d = date.fromisoformat(day).strftime("%d.%m.%Y")
    await c.message.edit_text(f"Дата: {d}\nВыберите время:",
                              reply_markup=kb_times(day))
    await c.answer()


@router.callback_query(F.data == "back:days")
async def back_days(c: CallbackQuery):
    await c.message.edit_text("Выберите дату съёмки:", reply_markup=kb_days())
    await c.answer()


@router.callback_query(F.data.startswith("time:"))
async def pick_time(c: CallbackQuery, state: FSMContext):
    sid = int(c.data.split(":")[1])
    with db() as conn:
        row = conn.execute("SELECT * FROM slots WHERE id=?", (sid,)).fetchone()
    if not row or row["busy"]:
        await c.answer("Это время уже занято", show_alert=True)
        return
    await state.update_data(slot_id=sid, day=row["day"], time=row["time"])
    await c.message.edit_text("Сколько часов аренды?", reply_markup=kb_durations())
    await c.answer()


@router.callback_query(F.data.startswith("dur:"))
async def pick_dur(c: CallbackQuery, state: FSMContext):
    await state.update_data(duration=DURATIONS[c.data.split(":")[1]])
    await c.message.edit_text("Цель съёмки?", reply_markup=kb_purposes())
    await c.answer()


@router.callback_query(F.data.startswith("pur:"))
async def pick_purpose(c: CallbackQuery, state: FSMContext):
    purpose = PURPOSES[int(c.data.split(":")[1])]
    await state.update_data(purpose=purpose, eq=[])
    await c.message.edit_text("Какое оборудование нужно?",
                              reply_markup=kb_equipment([]))
    await c.answer()


@router.callback_query(F.data.startswith("eq:"))
async def pick_eq(c: CallbackQuery, state: FSMContext):
    val = c.data.split(":")[1]
    data = await state.get_data()
    selected = data.get("eq", [])
    if val == "done":
        with db() as conn:
            names = []
            for i in selected:
                r = conn.execute("SELECT name FROM equipment WHERE id=?", (int(i),)).fetchone()
                if r:
                    names.append(r["name"])
        await state.update_data(equipment=", ".join(names) or "не требуется")
        await c.message.edit_text("Как вас зовут? Напишите имя.")
        await state.set_state(St.name)
        await c.answer()
        return
    if val in selected:
        selected.remove(val)
    else:
        selected.append(val)
    await state.update_data(eq=selected)
    await c.message.edit_reply_markup(reply_markup=kb_equipment(selected))
    await c.answer()


@router.message(StateFilter(St.name))
async def get_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await m.answer("Телефон для связи? Например +7 900 123-45-67")
    await state.set_state(St.phone)


@router.message(StateFilter(St.phone))
async def get_phone(m: Message, state: FSMContext):
    phone = m.text.strip()
    if len("".join(ch for ch in phone if ch.isdigit())) < 10:
        await m.answer("Проверьте номер — нужно не меньше 10 цифр.")
        return
    data = await state.get_data()
    await state.update_data(phone=phone)
    d = date.fromisoformat(data["day"]).strftime("%d.%m.%Y")
    text = (
        f"Проверьте заявку:\n\n"
        f"Дата: {d}\nВремя: {data['time']}\nДлительность: {data['duration']}\n"
        f"Цель: {data['purpose']}\nОборудование: {data.get('equipment','—')}\n"
        f"Имя: {data['name']}\nТелефон: {phone}\n\nВсё верно?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отправить заявку", callback_data="send:yes"),
        InlineKeyboardButton(text="Заново", callback_data="send:no"),
    ]])
    await m.answer(text, reply_markup=kb)
    await state.set_state(St.phone)


@router.callback_query(F.data == "send:no")
async def send_no(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Хорошо, начнём заново. Выберите дату:",
                              reply_markup=kb_days())
    await c.answer()


@router.callback_query(F.data == "send:yes")
async def send_yes(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("name"):
        await c.answer("Заявка устарела, начните заново: /start", show_alert=True)
        return
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO bookings(user_id,name,phone,day,time,duration,purpose,equipment)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (c.from_user.id, data["name"], data["phone"], data["day"], data["time"],
             data["duration"], data["purpose"], data.get("equipment", "—")))
        bid = cur.lastrowid
        slot_id = data.get("slot_id")
        if slot_id:
            conn.execute("UPDATE slots SET busy=1 WHERE id=?", (slot_id,))
    await c.message.edit_text(
        f"Заявка №{bid} принята. Администратор свяжется с вами для подтверждения.")
    await c.answer()

    aid = admin_id()
    d = date.fromisoformat(data["day"]).strftime("%d.%m.%Y")
    admin_text = (
        f"🔔 Новая заявка №{bid}\n\n"
        f"Дата: {d}\nВремя: {data['time']}\nДлительность: {data['duration']}\n"
        f"Цель: {data['purpose']}\nОборудование: {data.get('equipment','—')}\n"
        f"Имя: {data['name']}\nТелефон: {data['phone']}\n"
        f"Клиент: @{c.from_user.username or c.from_user.id}"
    )
    if aid:
        try:
            await c.bot.send_message(aid, admin_text, reply_markup=kb_admin_booking(bid))
        except Exception:
            pass
    await state.clear()


@router.callback_query(F.data.startswith(("ok:", "no:")))
async def admin_decision(c: CallbackQuery):
    if c.from_user.id != admin_id():
        await c.answer("Нет доступа", show_alert=True)
        return
    action, bid = c.data.split(":")
    bid = int(bid)
    with db() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
        if not row:
            await c.answer("Заявка не найдена", show_alert=True)
            return
        conn.execute("UPDATE bookings SET status=? WHERE id=?",
                     ("confirmed" if action == "ok" else "declined", bid))
    d = date.fromisoformat(row["day"]).strftime("%d.%m.%Y")
    if action == "ok":
        client_text = (
            f"✅ Бронь №{bid} подтверждена!\n\n"
            f"Дата: {d}\nВремя: {row['time']}\nДлительность: {row['duration']}\n"
            f"Оборудование: {row['equipment']}\n\n"
            f"Адрес: {STUDIO['address']}\nТелефон студии: {STUDIO['phone']}\n"
            f"Работаем: {STUDIO['hours']}"
        )
        await c.message.edit_text(c.message.text + "\n\n✅ Подтверждено")
    else:
        client_text = (f"К сожалению, бронь №{bid} на {d} {row['time']} недоступна. "
                       "Выберите другое время: /start")
        with db() as conn:
            conn.execute("UPDATE slots SET busy=0 WHERE day=? AND time=?",
                         (row["day"], row["time"]))
        await c.message.edit_text(c.message.text + "\n\n❌ Отклонено")
    try:
        await c.bot.send_message(row["user_id"], client_text)
    except Exception:
        pass
    await c.answer("Готово")


@router.message(Command("admin"))
async def admin_menu(m: Message):
    aid = admin_id()
    if aid is None:
        ADMIN_FILE.write_text(str(m.from_user.id), encoding="utf-8")
        aid = m.from_user.id
        await m.answer("Вы назначены администратором студии.")
    if m.from_user.id != aid:
        await m.answer("Нет доступа.")
        return
    await m.answer("Админ-панель:", reply_markup=kb_admin())


@router.callback_query(F.data == "adm:slots")
async def adm_slots(c: CallbackQuery):
    if c.from_user.id != admin_id():
        await c.answer("Нет доступа", show_alert=True); return
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM slots ORDER BY day, time LIMIT 30").fetchall()
    lines = [f"{date.fromisoformat(r['day']).strftime('%d.%m')} {r['time']} "
             f"{'— занято' if r['busy'] else '— свободно'}" for r in rows]
    await c.message.edit_text(
        "Слоты (ближайшие 30):\n" + "\n".join(lines) +
        "\n\n/add_slot ГГГГ-ММ-ДД ЧЧ:ММ — добавить\n/del_slot ГГГГ-ММ-ДД ЧЧ:ММ — удалить")
    await c.answer()


@router.message(Command("add_slot"))
async def add_slot(m: Message):
    if m.from_user.id != admin_id():
        return
    parts = (m.text or "").split()
    if len(parts) != 3:
        await m.answer("Формат: /add_slot 2026-12-01 15:00"); return
    try:
        date.fromisoformat(parts[1])
    except ValueError:
        await m.answer("Дата в формате ГГГГ-ММ-ДД"); return
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO slots(day,time,busy) VALUES (?,?,0)",
                     (parts[1], parts[2]))
    await m.answer(f"Слот {parts[1]} {parts[2]} добавлен.")


@router.message(Command("del_slot"))
async def del_slot(m: Message):
    if m.from_user.id != admin_id():
        return
    parts = (m.text or "").split()
    if len(parts) != 3:
        await m.answer("Формат: /del_slot 2026-12-01 15:00"); return
    with db() as conn:
        conn.execute("DELETE FROM slots WHERE day=? AND time=?", (parts[1], parts[2]))
    await m.answer("Удалено.")


@router.callback_query(F.data == "adm:eq")
async def adm_eq(c: CallbackQuery):
    if c.from_user.id != admin_id():
        await c.answer("Нет доступа", show_alert=True); return
    with db() as conn:
        rows = conn.execute("SELECT * FROM equipment ORDER BY id").fetchall()
    text = "Оборудование:\n" + "\n".join(
        f"{r['id']}. {r['name']} {'(вкл)' if r['active'] else '(выкл)'}" for r in rows)
    text += "\n\n/add_eq Название — добавить\n/off_eq ID — выключить\n/on_eq ID — включить"
    await c.message.edit_text(text)
    await c.answer()


@router.message(Command("add_eq"))
async def add_eq(m: Message):
    if m.from_user.id != admin_id():
        return
    name = (m.text or "").replace("/add_eq", "", 1).strip()
    if not name:
        await m.answer("Формат: /add_eq Софтбокс"); return
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO equipment(name) VALUES (?)", (name,))
    await m.answer(f"Добавлено: {name}")


@router.message(Command("off_eq"))
async def off_eq(m: Message):
    if m.from_user.id != admin_id():
        return
    try:
        eid = int((m.text or "").split()[1])
    except (IndexError, ValueError):
        await m.answer("Формат: /off_eq 3"); return
    with db() as conn:
        conn.execute("UPDATE equipment SET active=0 WHERE id=?", (eid,))
    await m.answer("Выключено.")


@router.message(Command("on_eq"))
async def on_eq(m: Message):
    if m.from_user.id != admin_id():
        return
    try:
        eid = int((m.text or "").split()[1])
    except (IndexError, ValueError):
        await m.answer("Формат: /on_eq 3"); return
    with db() as conn:
        conn.execute("UPDATE equipment SET active=1 WHERE id=?", (eid,))
    await m.answer("Включено.")


@router.callback_query(F.data == "adm:bookings")
async def adm_bookings(c: CallbackQuery):
    if c.from_user.id != admin_id():
        await c.answer("Нет доступа", show_alert=True); return
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        await c.message.edit_text("Заявок пока нет.")
        await c.answer(); return
    lines = []
    for r in rows:
        d = date.fromisoformat(r["day"]).strftime("%d.%m")
        lines.append(f"№{r['id']} {d} {r['time']} — {r['name']}, {r['phone']} [{r['status']}]")
    await c.message.edit_text("Последние заявки:\n" + "\n".join(lines))
    await c.answer()


async def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN не найден в .env")
    init_db()
    session = AiohttpSession(proxy=PROXY) if PROXY else None
    bot = Bot(TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(router)
    me = await bot.get_me()
    print(f"Бот запущен: @{me.username}", flush=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
