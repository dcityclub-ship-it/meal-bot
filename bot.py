import asyncio
import logging
import os
from datetime import time
import anthropic

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─── Настройка ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
# Время утренней рассылки (МСК = UTC+3, здесь UTC)
SEND_HOUR_UTC = 6   # 09:00 МСК

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
scheduler = AsyncIOScheduler()

# Храним chat_id всех пользователей (в памяти; для продакшна → БД)
subscribers: set[int] = set()


# ─── Вспомогательные функции ─────────────────────────────────────────────────

def ask_claude(prompt: str) -> str:
    """Отправляет запрос в Claude и возвращает текст ответа."""
    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def get_meal_suggestions() -> str:
    """Генерирует варианты блюд на день."""
    prompt = (
        "Предложи варианты блюд на один день: завтрак, обед и ужин.\n"
        "Требования:\n"
        "- Рецепты простые, время приготовления до 30 минут\n"
        "- Недорогие, доступные продукты\n"
        "- Для каждого приёма пищи дай 2 варианта на выбор\n\n"
        "Формат ответа строго такой (ничего лишнего):\n"
        "🌅 Завтрак:\n"
        "1. [Название блюда] — [одно предложение описание]\n"
        "2. [Название блюда] — [одно предложение описание]\n\n"
        "🍽 Обед:\n"
        "1. [Название блюда] — [одно предложение описание]\n"
        "2. [Название блюда] — [одно предложение описание]\n\n"
        "🌙 Ужин:\n"
        "1. [Название блюда] — [одно предложение описание]\n"
        "2. [Название блюда] — [одно предложение описание]"
    )
    return ask_claude(prompt)


def get_recipe_and_shopping(meal_name: str) -> str:
    """Генерирует рецепт и список покупок для выбранного блюда."""
    prompt = (
        f"Напиши подробный рецепт блюда «{meal_name}».\n"
        "Требования: быстро (до 30 мин), недорого, простые ингредиенты.\n\n"
        "Формат ответа:\n"
        "📋 Ингредиенты:\n"
        "- [список]\n\n"
        "👨‍🍳 Приготовление:\n"
        "1. [шаг]\n"
        "2. [шаг]\n"
        "...\n\n"
        "🛒 Что купить (если нет дома):\n"
        "- [список необходимых покупок]"
    )
    return ask_claude(prompt)


def build_meal_keyboard(suggestions_text: str) -> InlineKeyboardMarkup:
    """Парсит текст с блюдами и строит инлайн-клавиатуру."""
    buttons = []
    for line in suggestions_text.splitlines():
        line = line.strip()
        # Ищем строки вида "1. Название — описание" или "2. Название — описание"
        if line.startswith(("1.", "2.")):
            # Берём только название до тире
            parts = line[2:].strip().split("—")
            name = parts[0].strip()
            if name:
                buttons.append(
                    [InlineKeyboardButton(text=f"🍴 {name}", callback_data=f"meal:{name[:60]}")]
                )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── Хэндлеры ────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message):
    subscribers.add(message.chat.id)
    await message.answer(
        "👋 Привет! Я буду каждое утро присылать тебе варианты блюд на день.\n\n"
        "Ты выбираешь что приготовить — я пришлю рецепт и список покупок.\n\n"
        "Утренняя рассылка в 09:00 МСК 🕘\n\n"
        "Хочешь попробовать прямо сейчас? Напиши /menu"
    )


@dp.message(F.text == "/menu")
async def cmd_menu(message: Message):
    await message.answer("⏳ Генерирую варианты на сегодня, секунду...")
    try:
        suggestions = get_meal_suggestions()
        keyboard = build_meal_keyboard(suggestions)
        await message.answer(
            f"🗓 Меню на сегодня:\n\n{suggestions}\n\n"
            "👇 Выбери блюдо — получишь рецепт и список покупок:",
            reply_markup=keyboard,
        )
    except Exception as e:
        logging.error(e)
        await message.answer("Что-то пошло не так. Попробуй ещё раз через минуту.")


@dp.callback_query(F.data.startswith("meal:"))
async def on_meal_selected(callback: CallbackQuery):
    meal_name = callback.data.removeprefix("meal:")
    await callback.answer()
    await callback.message.answer(f"⏳ Готовлю рецепт для «{meal_name}»...")
    try:
        recipe = get_recipe_and_shopping(meal_name)
        await callback.message.answer(f"🍳 {meal_name}\n\n{recipe}")
    except Exception as e:
        logging.error(e)
        await callback.message.answer("Не удалось получить рецепт. Попробуй ещё раз.")


# ─── Утренняя рассылка ───────────────────────────────────────────────────────

async def morning_broadcast():
    if not subscribers:
        return
    try:
        suggestions = get_meal_suggestions()
        keyboard = build_meal_keyboard(suggestions)
        text = (
            f"☀️ Доброе утро! Вот варианты блюд на сегодня:\n\n{suggestions}\n\n"
            "👇 Выбери блюдо — пришлю рецепт и список покупок:"
        )
        for chat_id in list(subscribers):
            try:
                await bot.send_message(chat_id, text, reply_markup=keyboard)
            except Exception as e:
                logging.warning(f"Не удалось отправить {chat_id}: {e}")
    except Exception as e:
        logging.error(f"Ошибка рассылки: {e}")


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def main():
    scheduler.add_job(morning_broadcast, "cron", hour=SEND_HOUR_UTC, minute=0)
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
