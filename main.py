import logging
import os
import json
from datetime import datetime, timedelta

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    Update,
)
from telegram.ext import (
    Updater,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    Filters,
)

# === НАСТРОЙКИ ===

BOT_TOKEN = os.getenv("BOT_TOKEN")  # токен из переменных окружения
CHAT_ID = 366353826                # твой chat_id

# зима: ноябрь–март
WINTER_MONTHS = {11, 12, 1, 2, 3}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOG_FILE = "watering_logs.json"

# Текст кнопок в клавиатуре
SCHEDULE_BTN = "📅 Расписание поливов"
LOGS_BTN = "📘 Логи поливов"


# === ВСПОМОГАТЕЛЬНОЕ ===

def dt(y, m, d, h=9, minute=0):
    return datetime(y, m, d, h, minute)

def format_dt(d: datetime) -> str:
    return d.strftime("%d.%m.%Y %H:%M")

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(SCHEDULE_BTN)],
            [KeyboardButton(LOGS_BTN)],
        ],
        resize_keyboard=True,
    )


# === ДАННЫЕ О РАСТЕНИЯХ ===

PLANTS = {
    "zamioculcas": {
        "name": "Замиокулькас",
        "amount": "300 мл",
        "first_date": dt(2025, 12, 2),
        "interval_func": lambda current: timedelta(days=21),  # 3 недели
        "interval_text": "каждые 3–4 недели (в боте — каждые 3 недели)",
    },
    "bonsai": {
        "name": "Бонсай",
        "amount": "150 мл",
        "first_date": dt(2025, 11, 24),
        "interval_func": lambda current: timedelta(days=4) if current.month in WINTER_MONTHS else timedelta(days=2),
        "interval_text": "зимой каждые 4–5 дней (в боте — 4), летом каждые 1–2 дня (в боте — 2)",
        "autopot_offset": timedelta(days=45),
    },
    "aglaonema": {
        "name": "Аглаонема",
        "amount": "200–250 мл",
        "first_date": dt(2025, 11, 30),
        "interval_func": lambda current: timedelta(days=7),
        "interval_text": "раз в неделю",
    },
    "succulents": {
        "name": "Суккуленты",
        "amount": "100 мл",
        "first_date": dt(2025, 11, 26),
        "interval_func": lambda current: timedelta(days=10),
        "interval_text": "примерно раз в 1–2 недели (в боте — каждые 10 дней)",
    },
}


# === ЛОГИ ПОЛИВОВ ===

def load_logs():
    if not os.path.exists(LOG_FILE):
        return {plant_id: [] for plant_id in PLANTS}
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {plant_id: [] for plant_id in PLANTS}
    # гарантируем, что есть все растения
    for plant_id in PLANTS:
        data.setdefault(plant_id, [])
    return data

def save_logs():
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(LOGS, f, ensure_ascii=False, indent=2)


LOGS = load_logs()


# === ТЕКСТЫ И КНОПКИ ===

def plant_message(plant_id: str) -> str:
    p = PLANTS[plant_id]
    if plant_id == "succulents":
        return (
            f"🌵 {p['name']}\n\n"
            f"Проверь грунт. Если полностью сухой — полей {p['amount']}.\n"
            f"Отметь, когда полила."
        )
    else:
        return (
            f"🌿 {p['name']}\n\n"
            f"Пора полить: {p['amount']}.\n"
            f"Отметь, когда полила."
        )

def make_keyboard(plant_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Полила", callback_data=f"watered:{plant_id}"),
                InlineKeyboardButton("⏰ Не сейчас", callback_data=f"later:{plant_id}"),
            ]
        ]
    )


# === JOB'Ы ===

def schedule_next_watering(context: CallbackContext, plant_id: str, from_time: datetime = None):
    now = datetime.utcnow()
    base_time = from_time or now
    interval = PLANTS[plant_id]["interval_func"](base_time)
    next_time = base_time + interval

    logger.info(f"Next watering for {plant_id} at {next_time} (UTC)")

    context.job_queue.run_once(
        send_plant_reminder,
        when=max(0, (next_time - now).total_seconds()),
        context={"plant_id": plant_id},
        name=f"reminder:{plant_id}",
    )

def cancel_hourly_job(context: CallbackContext, plant_id: str):
    for job in context.job_queue.jobs():
        if job.name == f"hourly:{plant_id}":
            job.schedule_removal()

def send_plant_reminder(context: CallbackContext):
    plant_id = context.job.context["plant_id"]
    text = plant_message(plant_id)
    keyboard = make_keyboard(plant_id)

    context.bot.send_message(
        chat_id=CHAT_ID,
        text=text,
        reply_markup=keyboard,
    )

    # почасовые напоминания, пока не нажмёшь "Полила"
    context.job_queue.run_repeating(
        send_hourly_reminder,
        interval=3600,
        first=3600,
        context={"plant_id": plant_id},
        name=f"hourly:{plant_id}",
    )

def send_hourly_reminder(context: CallbackContext):
    plant_id = context.job.context["plant_id"]
    p = PLANTS[plant_id]
    context.bot.send_message(
        chat_id=CHAT_ID,
        text=f"⏰ Напоминание: полей {p['name'].lower()} (если ещё не полила).",
    )

def send_autopot_reminder(context: CallbackContext):
    context.bot.send_message(
        chat_id=CHAT_ID,
        text="🪴 Бонсай: прошло полтора месяца с первого полива, можно подумать про автополив.",
    )


# === КОМАНДЫ ===

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Привет! Я буду напоминать тебе поливать растения 🌿\n\n"
        "Доступно:\n"
        f"{SCHEDULE_BTN} — показать расписание поливов\n"
        f"{LOGS_BTN} — показать логи поливов",
        reply_markup=main_keyboard(),
    )


def get_next_dates(plant_id: str, count: int = 5):
    p = PLANTS[plant_id]
    now = datetime.utcnow()
    current = p["first_date"]

    while current < now:
        current += p["interval_func"](current)

    dates = []
    for _ in range(count):
        dates.append(current)
        current += p["interval_func"](current)

    return dates

def schedule_cmd(update: Update, context: CallbackContext):
    lines = ["📅 Расписание поливов и детали:\n"]

    for plant_id, p in PLANTS.items():
        lines.append(f"— {p['name']}")
        lines.append(f"  Частота: {p['interval_text']}")
        lines.append(f"  Объём: {p['amount']}")

        next_dates = get_next_dates(plant_id, count=5)
        pretty_dates = ", ".join(format_dt(d) for d in next_dates)
        lines.append(f"  Ближайшие поливы: {pretty_dates}\n")

    text = "\n".join(lines)
    update.message.reply_text(text)


def logs_cmd(update: Update, context: CallbackContext):
    lines = ["📘 Логи поливов:\n"]

    for plant_id, p in PLANTS.items():
        entries = LOGS.get(plant_id) or []
        lines.append(f"— {p['name']}")
        if entries:
            last_iso = entries[-1]
            try:
                last_dt = datetime.fromisoformat(last_iso)
                lines.append(f"  Последний полив: {format_dt(last_dt)}")
            except Exception:
                lines.append("  Последний полив: (ошибка формата даты)")
            lines.append(f"  Всего отмечено поливов: {len(entries)}\n")
        else:
            lines.append("  Ещё ни разу не отмечала полив через бота.\n")

    text = "\n".join(lines)
    update.message.reply_text(text)


# === ОБРАБОТЧИКИ КНОПОК И ТЕКСТА ===

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    data = query.data
    action, plant_id = data.split(":", 1)
    now = datetime.utcnow()

    if action == "watered":
        cancel_hourly_job(context, plant_id)

        # логируем полив
        LOGS.setdefault(plant_id, []).append(now.isoformat())
        save_logs()

        query.edit_message_text(
            text=f"✅ Отметила: {PLANTS[plant_id]['name']} полита."
        )

        schedule_next_watering(context, plant_id, from_time=now)

    elif action == "later":
        query.answer(text="Хорошо, напомню ещё раз позже 🙂", show_alert=False)
        # почасовые уже включены


def text_handler(update: Update, context: CallbackContext):
    text = (update.message.text or "").strip()

    if text == SCHEDULE_BTN:
        schedule_cmd(update, context)
    elif text == LOGS_BTN:
        logs_cmd(update, context)
    else:
        # можно добавить дефолтный ответ, но пока молчим
        pass


# === ЗАПУСК БОТА ===

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("schedule", schedule_cmd))
    dp.add_handler(CommandHandler("logs", logs_cmd))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_handler))

    now = datetime.utcnow()

    # стартовые напоминания
    for plant_id, p in PLANTS.items():
        first = p["first_date"]
        if first > now:
            delay = (first - now).total_seconds()
        else:
            delay = 5  # если дата в прошлом — стартуем почти сразу

        updater.job_queue.run_once(
            send_plant_reminder,
            when=delay,
            context={"plant_id": plant_id},
            name=f"reminder:{plant_id}",
        )

    # автополив бонсая
    bonsai = PLANTS["bonsai"]
    autopot_date = bonsai["first_date"] + bonsai["autopot_offset"]
    if autopot_date > now:
        updater.job_queue.run_once(
            send_autopot_reminder,
            when=(autopot_date - now).total_seconds(),
            name="autopot:bonsai",
        )

    logger.info("Bot started")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
