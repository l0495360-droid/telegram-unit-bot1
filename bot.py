import logging
import signal
import sys
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Обработчик остановки бота
def signal_handler(sig, frame):
    logging.info('Бот остановлен')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# Состояния для ConversationHandler
SELECT_CATEGORY, SELECT_UNIT_FROM, SELECT_UNIT_TO, ENTER_VALUE = range(4)

# Словарь с физическими величинами и единицами измерения
PHYSICAL_QUANTITIES = {
    "Длина": {
        "метр (м)": 1.0,
        "километр (км)": 1000.0,
        "сантиметр (см)": 0.01,
        "миллиметр (мм)": 0.001,
        "дюйм (in)": 0.0254,
        "фут (ft)": 0.3048,
        "ярд (yd)": 0.9144,
        "миля (mi)": 1609.34,
        "морская миля": 1852.0
    },
    "Масса": {
        "килограмм (кг)": 1.0,
        "грамм (г)": 0.001,
        "миллиграмм (мг)": 0.000001,
        "тонна (т)": 1000.0,
        "центнер (ц)": 100.0,
        "фунт (lb)": 0.453592,
        "унция (oz)": 0.0283495,
        "карат (ct)": 0.0002
    },
    "Время": {
        "секунда (с)": 1.0,
        "минута (мин)": 60.0,
        "час (ч)": 3600.0,
        "день": 86400.0,
        "неделя": 604800.0,
        "месяц": 2592000.0,
        "год": 31536000.0
    },
    "Температура": {
        "Цельсий (°C)": "celsius",
        "Фаренгейт (°F)": "fahrenheit", 
        "Кельвин (K)": "kelvin"
    },
    "Площадь": {
        "кв. метр (м²)": 1.0,
        "кв. километр (км²)": 1000000.0,
        "кв. сантиметр (см²)": 0.0001,
        "кв. миллиметр (мм²)": 0.000001,
        "гектар (га)": 10000.0,
        "акр": 4046.86,
        "сотка": 100.0,
        "кв. дюйм": 0.00064516,
        "кв. фут": 0.092903
    },
    "Объем": {
        "куб. метр (м³)": 1.0,
        "литр (л)": 0.001,
        "миллилитр (мл)": 0.000001,
        "куб. сантиметр (см³)": 0.000001,
        "галлон (gal)": 0.00378541,
        "баррель (bbl)": 0.158987,
        "куб. дюйм": 0.0000163871,
        "куб. фут": 0.0283168
    },
    "Скорость": {
        "метр/сек (м/с)": 1.0,
        "километр/час (км/ч)": 0.277778,
        "миля/час (mph)": 0.44704,
        "узел (kn)": 0.514444,
        "фут/сек (ft/s)": 0.3048
    },
    "Давление": {
        "паскаль (Па)": 1.0,
        "килопаскаль (кПа)": 1000.0,
        "бар": 100000.0,
        "атмосфера (атм)": 101325.0,
        "мм рт. ст.": 133.322,
        "psi": 6894.76
    }
}

# Функции для конвертации температуры
def convert_temperature(value, from_unit, to_unit):
    if from_unit == to_unit:
        return value
    
    # Конвертируем в Кельвины как промежуточную единицу
    if "Цельсий" in from_unit:
        kelvin = value + 273.15
    elif "Фаренгейт" in from_unit:
        kelvin = (value - 32) * 5/9 + 273.15
    elif "Кельвин" in from_unit:
        kelvin = value
    
    # Конвертируем из Кельвинов в целевую единицу
    if "Цельсий" in to_unit:
        return kelvin - 273.15
    elif "Фаренгейт" in to_unit:
        return (kelvin - 273.15) * 9/5 + 32
    elif "Кельвин" in to_unit:
        return kelvin

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я бот для конвертации физических величин.\n"
        "Используйте /convert чтобы начать перевод величин.\n"
        "Используйте /help для получения справки.\n\n"
        "📏 Например, я могу перевести:\n"
        "• Дюймы в сантиметры\n"
        "• Футы в метры\n"
        "• Фунты в килограммы\n"
        "• И многое другое!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help"""
    help_text = """
📋 **Доступные команды:**

/start - Начать работу с ботом
/convert - Конвертировать физические величины
/categories - Показать доступные категории величин
/help - Показать эту справку

🔧 **Как использовать:**
1. Нажмите /convert
2. Выберите категорию величин
3. Выберите исходную единицу измерения
4. Выберите целевую единицу измерения  
5. Введите значение для конвертации

📊 **Доступные категории:**
- Длина (метры, дюймы, футы, мили и др.)
- Масса (килограммы, фунты, унции и др.)  
- Время
- Температура
- Площадь
- Объем
- Скорость
- Давление

💡 **Примеры конвертаций:**
• 10 дюймов = 25.4 см
• 5 футов = 1.524 м
• 1 миля = 1.609 км
• 1 фунт = 0.454 кг
    """
    await update.message.reply_text(help_text)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать доступные категории"""
    categories = "\n".join([f"• {category}" for category in PHYSICAL_QUANTITIES.keys()])
    await update.message.reply_text(
        f"📊 Доступные категории величин:\n\n{categories}\n\n"
        "Используйте /convert чтобы начать конвертацию."
    )

async def convert_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало процесса конвертации"""
    keyboard = [
        [category] for category in PHYSICAL_QUANTITIES.keys()
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        "📊 Выберите категорию физической величины:",
        reply_markup=reply_markup
    )
    return SELECT_CATEGORY

async def select_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора категории"""
    category = update.message.text
    if category not in PHYSICAL_QUANTITIES:
        await update.message.reply_text("❌ Пожалуйста, выберите категорию из предложенных вариантов.")
        return SELECT_CATEGORY
    
    context.user_data['category'] = category
    units = list(PHYSICAL_QUANTITIES[category].keys())
    
    # Создаем клавиатуру с единицами измерения
    keyboard = [units[i:i+2] for i in range(0, len(units), 2)]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📏 Выберите исходную единицу измерения для {category}:",
        reply_markup=reply_markup
    )
    return SELECT_UNIT_FROM

async def select_unit_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора исходной единицы измерения"""
    unit_from = update.message.text
    category = context.user_data['category']
    
    if unit_from not in PHYSICAL_QUANTITIES[category]:
        await update.message.reply_text("❌ Пожалуйста, выберите единицу измерения из предложенных вариантов.")
        return SELECT_UNIT_FROM
    
    context.user_data['unit_from'] = unit_from
    units = list(PHYSICAL_QUANTITIES[category].keys())
    units.remove(unit_from)  # Убираем уже выбранную единицу
    
    # Создаем клавиатуру с оставшимися единицами
    keyboard = [units[i:i+2] for i in range(0, len(units), 2)]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        f"🎯 Выберите целевую единицу измерения:",
        reply_markup=reply_markup
    )
    return SELECT_UNIT_TO

async def select_unit_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора целевой единицы измерения"""
    unit_to = update.message.text
    category = context.user_data['category']
    
    if unit_to not in PHYSICAL_QUANTITIES[category]:
        await update.message.reply_text("❌ Пожалуйста, выберите единицу измерения из предложенных вариантов.")
        return SELECT_UNIT_TO
    
    context.user_data['unit_to'] = unit_to
    
    # Показываем примеры для популярных конвертаций
    examples = ""
    if category == "Длина":
        if "дюйм" in context.user_data['unit_from'] and "сантиметр" in unit_to:
            examples = "\n💡 Пример: 10 дюймов = 25.4 см"
        elif "фут" in context.user_data['unit_from'] and "метр" in unit_to:
            examples = "\n💡 Пример: 6 футов = 1.8288 м"
    
    await update.message.reply_text(
        f"🔢 Введите значение для конвертации:\n"
        f"Из: {context.user_data['unit_from']}\n"
        f"В: {unit_to}{examples}\n\n"
        "Можно вводить целые числа и десятичные дроби: 10, 15.5, -40, 0.25",
        reply_markup=None  # Убираем клавиатуру для ввода числа
    )
    return ENTER_VALUE

async def enter_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода значения и выполнение конвертации"""
    try:
        value_text = update.message.text.replace(',', '.')  # Заменяем запятые на точки
        value = float(value_text)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите корректное числовое значение.\nПример: 10, 15.5, 0.25")
        return ENTER_VALUE
    
    category = context.user_data['category']
    unit_from = context.user_data['unit_from']
    unit_to = context.user_data['unit_to']
    
    # Выполняем конвертацию
    if category == "Температура":
        # Особый случай для температуры
        result = convert_temperature(value, unit_from, unit_to)
    else:
        # Конвертация для остальных величин
        factor_from = PHYSICAL_QUANTITIES[category][unit_from]
        factor_to = PHYSICAL_QUANTITIES[category][unit_to]
        result = value * factor_from / factor_to
    
    # Форматируем результат
    if abs(result) < 0.0001 or abs(result) > 1000000:
        result_str = f"{result:.6e}"
    else:
        result_str = f"{result:.8f}".rstrip('0').rstrip('.')
        if '.' in result_str:
            # Ограничиваем количество знаков после запятой
            parts = result_str.split('.')
            if len(parts[1]) > 6:
                result_str = f"{result:.6f}".rstrip('0').rstrip('.')
    
    # Создаем красивый вывод
    await update.message.reply_text(
        f"✅ **Результат конвертации:**\n\n"
        f"```\n{value} {unit_from} = {result_str} {unit_to}\n```\n"
        f"**Категория:** {category}\n\n"
        "Используйте /convert для новой конвертации",
        parse_mode='Markdown'
    )
    
    # Очищаем данные пользователя
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена конвертации"""
    await update.message.reply_text(
        "Конвертация отменена.\n"
        "Используйте /convert чтобы начать заново."
    )
    context.user_data.clear()
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.message:
        await update.message.reply_text(
            "❌ Произошла ошибка. Пожалуйста, попробуйте снова.\n"
            "Используйте /help для получения справки."
        )

def main() -> None:
    """Запуск бота"""
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("categories", show_categories))
    
    # ConversationHandler для конвертации
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("convert", convert_start)],
        states={
            SELECT_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_category)],
            SELECT_UNIT_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_unit_from)],
            SELECT_UNIT_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_unit_to)],
            ENTER_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    application.add_handler(conv_handler)
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logging.info("🤖 Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
