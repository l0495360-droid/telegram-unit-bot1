import os
import logging
import re
import math
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from decimal import Decimal, InvalidOperation
import sqlite3
from contextlib import contextmanager
import json
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, 
    ConversationHandler, CallbackQueryHandler, JobQueue
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('converter_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Получаем токен из переменных окружения
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Проверка наличия токена
if not TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не найден!")
    logger.error("Добавьте TELEGRAM_BOT_TOKEN в настройки репозитория")
    exit(1)

logger.info("✅ Токен успешно получен")

# Состояния для ConversationHandler
SELECT_CATEGORY, SELECT_UNIT_FROM, SELECT_UNIT_TO, ENTER_VALUE, SAVE_FAVORITE = range(5)

# Инициализация базы данных
def init_database():
    """Инициализация базы данных SQLite"""
    with get_db_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_favorites (
                user_id INTEGER,
                favorite_name TEXT,
                from_unit TEXT,
                to_unit TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, favorite_name)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS conversion_history (
                user_id INTEGER,
                from_value REAL,
                from_unit TEXT,
                to_value REAL,
                to_unit TEXT,
                converted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                conversions_count INTEGER DEFAULT 0,
                favorite_category TEXT,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

@contextmanager
def get_db_connection():
    """Контекстный менеджер для подключения к БД"""
    conn = sqlite3.connect('converter_bot.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# Базовые единицы измерения
BASE_UNITS = {
    "Длина": "метр (м)",
    "Древнерусские меры длины": "метр (м)", 
    "Масса": "килограмм (кг)",
    "Время": "секунда (с)",
    "Температура": "Цельсий (°C)",
    "Площадь": "кв. метр (м²)",
    "Объем": "куб. метр (м³)",
    "Скорость": "метр/сек (м/с)",
    "Давление": "паскаль (Па)",
    "Информация": "бит (bit)",
    "Скорость передачи данных": "бит/сек (bps)",
    "Энергия": "джоуль (Дж)",
    "Мощность": "ватт (Вт)",
    "Углы": "градус (°)"
}

# Расширенный словарь с физическими величинами
PHYSICAL_QUANTITIES = {
    "Длина": {
        "метр (м)": 1.0,
        "километр (км)": 1000.0,
        "сантиметр (см)": 0.01,
        "миллиметр (мм)": 0.001,
        "микрометр (мкм)": 1e-6,
        "нанометр (нм)": 1e-9,
        "дюйм (in)": 0.0254,
        "фут (ft)": 0.3048,
        "ярд (yd)": 0.9144,
        "миля (mi)": 1609.34,
        "морская миля": 1852.0,
        "астрономическая единица (а.е.)": 1.496e11,
        "световой год (ly)": 9.461e15,
        "парсек (pc)": 3.086e16
    },
    "Древнерусские меры длины": {
        "вершок": 0.04445,
        "пядь": 0.1778,
        "локоть": 0.4572,
        "аршин": 0.7112,
        "сажень": 2.1336,
        "верста": 1066.8,
        "поприще": 1500.0,
        "точка": 0.000254,
        "линия": 0.00254,
        "перст": 0.01905
    },
    "Масса": {
        "килограмм (кг)": 1.0,
        "грамм (г)": 0.001,
        "миллиграмм (мг)": 1e-6,
        "микрограмм (мкг)": 1e-9,
        "тонна (т)": 1000.0,
        "центнер (ц)": 100.0,
        "фунт (lb)": 0.453592,
        "унция (oz)": 0.0283495,
        "карат (ct)": 0.0002,
        "пуд": 16.3805,
        "берковец": 163.805,
        "золотник": 0.004266
    },
    "Время": {
        "секунда (с)": 1.0,
        "миллисекунда (мс)": 0.001,
        "микросекунда (мкс)": 1e-6,
        "минута (мин)": 60.0,
        "час (ч)": 3600.0,
        "день": 86400.0,
        "неделя": 604800.0,
        "месяц (30 дней)": 2592000.0,
        "год (365 дней)": 31536000.0,
        "век": 3.1536e9
    },
    "Температура": {
        "Цельсий (°C)": "celsius",
        "Фаренгейт (°F)": "fahrenheit", 
        "Кельвин (K)": "kelvin",
        "Реомюр (°Ré)": "reaumur",
        "Ранкин (°R)": "rankine"
    },
    "Площадь": {
        "кв. метр (м²)": 1.0,
        "кв. километр (км²)": 1e6,
        "кв. сантиметр (см²)": 1e-4,
        "кв. миллиметр (мм²)": 1e-6,
        "гектар (га)": 10000.0,
        "акр": 4046.86,
        "сотка (ар)": 100.0,
        "кв. дюйм": 0.00064516,
        "кв. фут": 0.092903,
        "кв. миля": 2.59e6,
        "десятина": 10925.0
    },
    "Объем": {
        "куб. метр (м³)": 1.0,
        "литр (л)": 0.001,
        "миллилитр (мл)": 1e-6,
        "куб. сантиметр (см³)": 1e-6,
        "куб. дециметр (дм³)": 0.001,
        "галлон (US)": 0.00378541,
        "галлон (UK)": 0.00454609,
        "баррель нефтяной": 0.158987,
        "куб. дюйм": 1.6387e-5,
        "куб. фут": 0.0283168,
        "пинта (US)": 0.000473176,
        "пинта (UK)": 0.000568261,
        "ведро": 0.012299,
        "бочка": 0.491976,
        "штоф": 0.0012299
    },
    "Скорость": {
        "метр/сек (м/с)": 1.0,
        "километр/час (км/ч)": 0.277778,
        "миля/час (mph)": 0.44704,
        "узел (kn)": 0.514444,
        "фут/сек (ft/s)": 0.3048,
        "маховое число (M)": 340.3,
        "скорость света (c)": 299792458.0
    },
    "Давление": {
        "паскаль (Па)": 1.0,
        "килопаскаль (кПа)": 1000.0,
        "мегапаскаль (МПа)": 1e6,
        "бар": 1e5,
        "миллибар (мбар)": 100.0,
        "атмосфера (атм)": 101325.0,
        "мм рт. ст. (торр)": 133.322,
        "psi (фунт/кв.дюйм)": 6894.76,
        "техн. атмосфера (ат)": 98066.5
    },
    "Информация": {
        "бит (bit)": 1.0,
        "байт (byte)": 8.0,
        "килобит (Kbit)": 1024.0,
        "килобайт (KB)": 8192.0,
        "мегабит (Mbit)": 1048576.0,
        "мегабайт (MB)": 8388608.0,
        "гигабит (Gbit)": 1073741824.0,
        "гигабайт (GB)": 8589934592.0,
        "терабит (Tbit)": 1099511627776.0,
        "терабайт (TB)": 8796093022208.0,
        "петабит (Pbit)": 1125899906842624.0,
        "петабайт (PB)": 9007199254740992.0
    },
    "Скорость передачи данных": {
        "бит/сек (bps)": 1.0,
        "килобит/сек (Kbps)": 1024.0,
        "мегабит/сек (Mbps)": 1048576.0,
        "гигабит/сек (Gbps)": 1073741824.0,
        "терабит/сек (Tbps)": 1099511627776.0,
        "байт/сек (Bps)": 8.0,
        "килобайт/сек (KBps)": 8192.0,
        "мегабайт/сек (MBps)": 8388608.0,
        "гигабайт/сек (GBps)": 8589934592.0,
        "терабайт/сек (TBps)": 8796093022208.0
    },
    "Энергия": {
        "джоуль (Дж)": 1.0,
        "килоджоуль (кДж)": 1000.0,
        "мегаджоуль (МДж)": 1e6,
        "калория (кал)": 4.184,
        "килокалория (ккал)": 4184.0,
        "ватт-час (Вт·ч)": 3600.0,
        "киловатт-час (кВт·ч)": 3.6e6,
        "электронвольт (эВ)": 1.602e-19,
        "британская тепловая единица (BTU)": 1055.06
    },
    "Мощность": {
        "ватт (Вт)": 1.0,
        "киловатт (кВт)": 1000.0,
        "мегаватт (МВт)": 1e6,
        "лошадиная сила (л.с.)": 735.499,
        "лошадиная сила (hp)": 745.7,
        "калория/сек": 4.184,
        "BTU/час": 0.293071
    },
    "Углы": {
        "градус (°)": 1.0,
        "радиан (rad)": 57.2958,
        "минута угловая (′)": 1/60,
        "секунда угловая (″)": 1/3600,
        "оборот (rev)": 360.0,
        "град (gon)": 0.9
    }
}

# Группы совместимых категорий
COMPATIBLE_CATEGORIES = {
    "Длина": ["Длина", "Древнерусские меры длины"],
    "Древнерусские меры длины": ["Длина", "Древнерусские меры длины"],
    "Масса": ["Масса"],
    "Время": ["Время"],
    "Температура": ["Температура"],
    "Площадь": ["Площадь"],
    "Объем": ["Объем"],
    "Скорость": ["Скорость"],
    "Давление": ["Давление"],
    "Информация": ["Информация"],
    "Скорость передачи данных": ["Скорость передачи данных"],
    "Энергия": ["Энергия"],
    "Мощность": ["Мощность"],
    "Углы": ["Углы"]
}

# Популярные конвертации для быстрого доступа
POPULAR_CONVERSIONS = {
    "📏 Дюймы в см": ("10 дюйм (in)", "сантиметр (см)"),
    "📏 Футы в метры": ("6 фут (ft)", "метр (м)"),
    "⚖️ Фунты в кг": ("1 фунт (lb)", "килограмм (кг)"),
    "🌡️ °F в °C": ("32 Фаренгейт (°F)", "Цельсий (°C)"),
    "💻 Мбит в МБ": ("100 мегабит/сек (Mbps)", "мегабайт/сек (MBps)"),
    "📊 Байты в биты": ("1 байт (byte)", "бит (bit)"),
    "🛣️ Версты в км": ("1 верста", "километр (км)"),
    "📐 Сажени в метры": ("1 сажень", "метр (м)")
}

class UnitConverter:
    """Класс для конвертации единиц измерения"""
    
    @staticmethod
    def convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
        """Конвертация температуры"""
        if from_unit == to_unit:
            return value
        
        # Конвертируем в Кельвины как промежуточную единицу
        if "Цельсий" in from_unit:
            kelvin = value + 273.15
        elif "Фаренгейт" in from_unit:
            kelvin = (value - 32) * 5/9 + 273.15
        elif "Кельвин" in from_unit:
            kelvin = value
        elif "Реомюр" in from_unit:
            kelvin = value * 1.25 + 273.15
        elif "Ранкин" in from_unit:
            kelvin = value * 5/9
        
        # Проверка абсолютного нуля
        if kelvin < 0:
            raise ValueError("❌ Температура не может быть ниже абсолютного нуля (-273.15°C)")
        
        # Конвертируем из Кельвинов в целевую единицу
        if "Цельсий" in to_unit:
            return kelvin - 273.15
        elif "Фаренгейт" in to_unit:
            return (kelvin - 273.15) * 9/5 + 32
        elif "Кельвин" in to_unit:
            return kelvin
        elif "Реомюр" in to_unit:
            return (kelvin - 273.15) * 0.8
        elif "Ранкин" in to_unit:
            return kelvin * 9/5
    
    @staticmethod
    def convert_standard(value: float, from_unit: str, to_unit: str, from_category: str) -> float:
        """Конвертация стандартных величин"""
        # Находим коэффициенты для обеих единиц
        factor_from = None
        factor_to = None
        
        for category in COMPATIBLE_CATEGORIES.get(from_category, []):
            if from_unit in PHYSICAL_QUANTITIES.get(category, {}):
                factor_from = PHYSICAL_QUANTITIES[category][from_unit]
            if to_unit in PHYSICAL_QUANTITIES.get(category, {}):
                factor_to = PHYSICAL_QUANTITIES[category][to_unit]
        
        if factor_from is None or factor_to is None:
            raise ValueError(f"❌ Не удалось найти коэффициенты для конвертации")
        
        # Выполняем конвертацию
        return value * factor_from / factor_to
    
    @staticmethod
    def format_result(value: float) -> str:
        """Форматирование результата для лучшей читаемости"""
        if value == 0:
            return "0"
        
        abs_value = abs(value)
        
        if abs_value < 1e-6 or abs_value > 1e9:
            # Научная нотация для очень больших/маленьких чисел
            return f"{value:.6e}".replace('e-0', 'e-').replace('e+0', 'e+')
        elif abs_value < 0.001:
            return f"{value:.8f}".rstrip('0').rstrip('.')
        elif abs_value < 1:
            return f"{value:.6f}".rstrip('0').rstrip('.')
        elif abs_value < 1000:
            return f"{value:.4f}".rstrip('0').rstrip('.')
        else:
            return f"{value:.2f}".rstrip('0').rstrip('.')
    
    @staticmethod
    def validate_input(text: str) -> Tuple[bool, Optional[float], Optional[str]]:
        """Валидация введенного значения"""
        try:
            # Заменяем запятые на точки и убираем пробелы
            cleaned = text.replace(',', '.').replace(' ', '')
            
            # Проверяем на специальные значения
            if cleaned.lower() in ['pi', 'π']:
                return True, math.pi, None
            elif cleaned.lower() == 'e':
                return True, math.e, None
            
            # Проверяем дроби вида 1/2, 3/4 и т.д.
            if '/' in cleaned:
                parts = cleaned.split('/')
                if len(parts) == 2:
                    numerator = float(parts[0])
                    denominator = float(parts[1])
                    if denominator == 0:
                        return False, None, "❌ Деление на ноль невозможно"
                    return True, numerator / denominator, None
            
            value = float(cleaned)
            return True, value, None
            
        except ValueError:
            return False, None, "❌ Пожалуйста, введите корректное числовое значение\nПример: 10, 15.5, 1/2, -40, 0.25, pi"

class DatabaseManager:
    """Менеджер для работы с базой данных"""
    
    @staticmethod
    def save_conversion_history(user_id: int, from_value: float, from_unit: str, 
                              to_value: float, to_unit: str):
        """Сохранение истории конвертаций"""
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO conversion_history (user_id, from_value, from_unit, to_value, to_unit) VALUES (?, ?, ?, ?, ?)",
                (user_id, from_value, from_unit, to_value, to_unit)
            )
            
            # Обновляем статистику пользователя
            conn.execute('''
                INSERT OR REPLACE INTO user_stats (user_id, conversions_count, last_activity)
                VALUES (?, 
                    COALESCE((SELECT conversions_count FROM user_stats WHERE user_id = ?), 0) + 1,
                    CURRENT_TIMESTAMP)
            ''', (user_id, user_id))
    
    @staticmethod
    def get_user_favorites(user_id: int) -> List[Tuple]:
        """Получение избранных конвертаций пользователя"""
        with get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT favorite_name, from_unit, to_unit FROM user_favorites WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            return cursor.fetchall()
    
    @staticmethod
    def save_favorite(user_id: int, favorite_name: str, from_unit: str, to_unit: str):
        """Сохранение избранной конвертации"""
        with get_db_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_favorites (user_id, favorite_name, from_unit, to_unit) VALUES (?, ?, ?, ?)",
                (user_id, favorite_name, from_unit, to_unit)
            )
    
    @staticmethod
    def delete_favorite(user_id: int, favorite_name: str):
        """Удаление избранной конвертации"""
        with get_db_connection() as conn:
            conn.execute(
                "DELETE FROM user_favorites WHERE user_id = ? AND favorite_name = ?",
                (user_id, favorite_name)
            )
    
    @staticmethod
    def get_user_stats(user_id: int):
        """Получение статистики пользователя"""
        with get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT conversions_count, last_activity FROM user_stats WHERE user_id = ?",
                (user_id,)
            )
            return cursor.fetchone()
    
    @staticmethod
    def get_recent_conversions(user_id: int, limit: int = 5):
        """Получение последних конвертаций пользователя"""
        with get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT from_value, from_unit, to_value, to_unit, converted_at "
                "FROM conversion_history WHERE user_id = ? ORDER BY converted_at DESC LIMIT ?",
                (user_id, limit)
            )
            return cursor.fetchall()

class KeyboardManager:
    """Менеджер для создания клавиатур"""
    
    @staticmethod
    def create_main_keyboard():
        """Создание основной клавиатуры"""
        keyboard = [
            ["📊 Конвертировать", "⭐ Избранное"],
            ["📈 История", "🚀 Быстрые конвертации"],
            ["📚 Категории", "ℹ️ Помощь"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)
    
    @staticmethod
    def create_categories_keyboard():
        """Создание клавиатуры с категориями"""
        categories = list(PHYSICAL_QUANTITIES.keys())
        keyboard = [categories[i:i+2] for i in range(0, len(categories), 2)]
        keyboard.append(["🔙 Назад"])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    @staticmethod
    def create_units_keyboard(units: List[str], back_button: bool = True):
        """Создание клавиатуры с единицами измерения"""
        keyboard = [units[i:i+2] for i in range(0, len(units), 2)]
        if back_button:
            keyboard.append(["🔙 Назад"])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    @staticmethod
    def create_popular_conversions_keyboard():
        """Создание клавиатуры с популярными конвертациями"""
        keyboard = []
        for name, (from_unit, to_unit) in POPULAR_CONVERSIONS.items():
            keyboard.append([name])
        keyboard.append(["🔙 Назад"])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    @staticmethod
    def create_favorites_keyboard(favorites: List[Tuple]):
        """Создание клавиатуры с избранными конвертациями"""
        keyboard = []
        for favorite in favorites:
            keyboard.append([f"⭐ {favorite[0]}"])
        keyboard.append(["➕ Добавить в избранное"])
        keyboard.append(["🔙 Назад"])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

class BotHandlers:
    """Класс с обработчиками бота"""
    
    def __init__(self):
        self.converter = UnitConverter()
        self.db = DatabaseManager()
        self.keyboard = KeyboardManager()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /start"""
        user = update.effective_user
        
        welcome_text = f"""
👋 Привет, {user.first_name}!

🤖 Я - продвинутый бот для конвертации физических величин с поддержкой более 200 единиц измерения!

✨ **Основные возможности:**
• 📊 Конвертация между различными системами измерений
• ⭐ Избранные конвертации для быстрого доступа
• 📈 История ваших конвертаций
• 🚀 Быстрые популярные конвертации
• 🔍 Поддержка древнерусских мер и специальных единиц

🎯 **Начните с команды /convert или используйте кнопки ниже!**

💡 **Совет:** Используйте кнопку "🚀 Быстрые конвертации" для часто используемых преобразований.
        """
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=self.keyboard.create_main_keyboard(),
            parse_mode='Markdown'
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /help"""
        help_text = """
📋 **Доступные команды:**

/start - Начать работу с ботом
/convert - Конвертировать физические величины
/favorites - Управление избранными конвертациями
/history - История конвертаций
/stats - Статистика использования
/categories - Показать доступные категории
/help - Показать эту справку

🔄 **Как использовать:**

1. **Обычная конвертация:**
   - Нажмите "📊 Конвертировать"
   - Выберите категорию
   - Выберите исходную и целевую единицы
   - Введите значение

2. **Быстрые конвертации:**
   - Нажмите "🚀 Быстрые конвертации"
   - Выберите нужный вариант

3. **Избранное:**
   - Сохраняйте часто используемые конвертации
   - Быстрый доступ из меню "⭐ Избранное"

🔢 **Поддерживаемые форматы ввода:**
- Целые числа: 10, -5, 1000
- Десятичные дроби: 15.5, 0.25, -3.14
- Дроби: 1/2, 3/4, 15/16
- Константы: pi, π, e

📊 **Новые категории:**
- 🔋 Энергия (джоули, калории, кВт·ч)
- ⚡ Мощность (ватты, лошадиные силы)
- 📐 Углы (градусы, радианы)
- И многое другое!
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def show_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать доступные категории"""
        categories_text = "📚 *Доступные категории величин:*\n\n"
        
        for i, category in enumerate(PHYSICAL_QUANTITIES.keys(), 1):
            units_count = len(PHYSICAL_QUANTITIES[category])
            categories_text += f"• *{category}* - {units_count} единиц\n"
        
        categories_text += "\n🎯 Используйте кнопку \"📊 Конвертировать\" чтобы начать!"
        
        await update.message.reply_text(
            categories_text,
            reply_markup=self.keyboard.create_main_keyboard(),
            parse_mode='Markdown'
        )
    
    async def convert_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начало процесса конвертации"""
        await update.message.reply_text(
            "📊 Выберите категорию физической величины:",
            reply_markup=self.keyboard.create_categories_keyboard()
        )
        return SELECT_CATEGORY
    
    async def select_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка выбора категории"""
        category = update.message.text
        
        if category == "🔙 Назад":
            await update.message.reply_text(
                "Главное меню:",
                reply_markup=self.keyboard.create_main_keyboard()
            )
            return ConversationHandler.END
        
        if category not in PHYSICAL_QUANTITIES:
            await update.message.reply_text("❌ Пожалуйста, выберите категорию из предложенных вариантов.")
            return SELECT_CATEGORY
        
        context.user_data['category'] = category
        units = list(PHYSICAL_QUANTITIES[category].keys())
        
        await update.message.reply_text(
            f"📏 Выберите исходную единицу измерения для *{category}*:",
            reply_markup=self.keyboard.create_units_keyboard(units),
            parse_mode='Markdown'
        )
        return SELECT_UNIT_FROM
    
    async def select_unit_from(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка выбора исходной единицы измерения"""
        unit_from = update.message.text
        
        if unit_from == "🔙 Назад":
            await update.message.reply_text(
                "📊 Выберите категорию:",
                reply_markup=self.keyboard.create_categories_keyboard()
            )
            return SELECT_CATEGORY
        
        category = context.user_data['category']
        
        if unit_from not in PHYSICAL_QUANTITIES[category]:
            await update.message.reply_text("❌ Пожалуйста, выберите единицу измерения из предложенных вариантов.")
            return SELECT_UNIT_FROM
        
        context.user_data['unit_from'] = unit_from
        
        # Получаем все совместимые единицы
        compatible_units = self._get_compatible_units(category)
        units_list = list(compatible_units.keys())
        
        # Убираем уже выбранную единицу
        if unit_from in units_list:
            units_list.remove(unit_from)
        
        await update.message.reply_text(
            f"🎯 Выберите целевую единицу измерения:\n"
            f"*(доступны единицы из совместимых категорий)*",
            reply_markup=self.keyboard.create_units_keyboard(units_list),
            parse_mode='Markdown'
        )
        return SELECT_UNIT_TO
    
    async def select_unit_to(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка выбора целевой единицы измерения"""
        unit_to = update.message.text
        
        if unit_to == "🔙 Назад":
            category = context.user_data['category']
            units = list(PHYSICAL_QUANTITIES[category].keys())
            
            await update.message.reply_text(
                f"📏 Выберите исходную единицу измерения для *{category}*:",
                reply_markup=self.keyboard.create_units_keyboard(units),
                parse_mode='Markdown'
            )
            return SELECT_UNIT_FROM
        
        from_category = context.user_data['category']
        
        # Проверяем, что выбранная единица совместима
        compatible_units = self._get_compatible_units(from_category)
        if unit_to not in compatible_units:
            await update.message.reply_text(
                "❌ Эта единица несовместима с выбранной исходной единицей.\n"
                "Пожалуйста, выберите единицу из предложенных вариантов."
            )
            return SELECT_UNIT_TO
        
        context.user_data['unit_to'] = unit_to
        
        # Определяем категорию целевой единицы
        to_category = self._find_unit_category(unit_to)
        context.user_data['to_category'] = to_category
        
        # Показываем подсказки
        hint = self._get_conversion_hint(context.user_data['unit_from'], unit_to)
        
        await update.message.reply_text(
            f"🔢 *Введите значение для конвертации:*\n\n"
            f"*Из:* {context.user_data['unit_from']} ({from_category})\n"
            f"*В:* {unit_to} ({to_category})\n\n"
            f"{hint}\n"
            f"*Можно вводить:* 10, 15.5, 1/2, -40, 0.25, pi",
            reply_markup=None,
            parse_mode='Markdown'
        )
        return ENTER_VALUE
    
    async def enter_value(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка ввода значения и выполнение конвертации"""
        user_id = update.effective_user.id
        value_text = update.message.text
        
        # Валидация ввода
        is_valid, value, error_message = self.converter.validate_input(value_text)
        
        if not is_valid:
            await update.message.reply_text(error_message)
            return ENTER_VALUE
        
        from_category = context.user_data['category']
        unit_from = context.user_data['unit_from']
        unit_to = context.user_data['unit_to']
        to_category = context.user_data.get('to_category', 'Неизвестно')
        
        try:
            # Выполняем конвертацию
            if from_category == "Температура" or to_category == "Температура":
                result = self.converter.convert_temperature(value, unit_from, unit_to)
            else:
                result = self.converter.convert_standard(value, unit_from, unit_to, from_category)
            
            # Форматируем результат
            result_str = self.converter.format_result(result)
            
            # Сохраняем в историю
            self.db.save_conversion_history(user_id, value, unit_from, result, unit_to)
            
            # Создаем красивый вывод
            category_info = ""
            if from_category != to_category:
                category_info = f"*🔀 Конвертация между системами:* {from_category} → {to_category}\n\n"
            
            response_text = (
                f"✅ *Результат конвертации:*\n\n"
                f"{category_info}"
                f"```\n{value} {unit_from} = {result_str} {unit_to}\n```\n"
                f"💾 *Конвертация сохранена в истории*\n"
                f"🔄 Используйте кнопки ниже для новых операций"
            )
            
            # Сохраняем данные для возможного добавления в избранное
            context.user_data['last_conversion'] = {
                'from_value': value,
                'from_unit': unit_from,
                'to_value': result,
                'to_unit': unit_to
            }
            
            keyboard = [
                ["⭐ Добавить в избранное", "📊 Новая конвертация"],
                ["🚀 Быстрые конвертации", "📈 История"],
                ["🔙 Главное меню"]
            ]
            
            await update.message.reply_text(
                response_text,
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
                parse_mode='Markdown'
            )
            
            return SAVE_FAVORITE
            
        except Exception as e:
            logger.error(f"Ошибка конвертации: {e}")
            await update.message.reply_text(
                f"❌ Произошла ошибка при конвертации: {str(e)}\n"
                f"Пожалуйста, попробуйте снова.",
                reply_markup=self.keyboard.create_main_keyboard()
            )
            return ConversationHandler.END
    
    async def save_favorite_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработчик для сохранения в избранное"""
        user_input = update.message.text
        
        if user_input == "🔙 Главное меню":
            await update.message.reply_text(
                "Главное меню:",
                reply_markup=self.keyboard.create_main_keyboard()
            )
            return ConversationHandler.END
        
        elif user_input == "📊 Новая конвертация":
            await update.message.reply_text(
                "📊 Выберите категорию:",
                reply_markup=self.keyboard.create_categories_keyboard()
            )
            return SELECT_CATEGORY
        
        elif user_input == "🚀 Быстрые конвертации":
            await self.show_popular_conversions(update, context)
            return ConversationHandler.END
        
        elif user_input == "📈 История":
            await self.show_history(update, context)
            return ConversationHandler.END
        
        elif user_input == "⭐ Добавить в избранное":
            if 'last_conversion' not in context.user_data:
                await update.message.reply_text("❌ Нет данных для сохранения в избранное")
                return SAVE_FAVORITE
            
            conversion = context.user_data['last_conversion']
            favorite_name = f"{conversion['from_unit']} → {conversion['to_unit']}"
            
            self.db.save_favorite(
                update.effective_user.id,
                favorite_name,
                conversion['from_unit'],
                conversion['to_unit']
            )
            
            await update.message.reply_text(
                f"✅ Конвертация добавлена в избранное как:\n\"{favorite_name}\"",
                reply_markup=self.keyboard.create_main_keyboard()
            )
            return ConversationHandler.END
        
        else:
            await update.message.reply_text(
                "Пожалуйста, используйте кнопки для выбора действия",
                reply_markup=self.keyboard.create_main_keyboard()
            )
            return ConversationHandler.END
    
    async def show_favorites(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать избранные конвертации"""
        user_id = update.effective_user.id
        favorites = self.db.get_user_favorites(user_id)
        
        if not favorites:
            await update.message.reply_text(
                "⭐ У вас пока нет избранных конвертаций.\n\n"
                "Чтобы добавить конвертацию в избранное:\n"
                "1. Выполните обычную конвертацию\n"
                "2. Нажмите кнопку \"⭐ Добавить в избранное\"",
                reply_markup=self.keyboard.create_main_keyboard()
            )
            return
        
        favorites_text = "⭐ *Ваши избранные конвертации:*\n\n"
        for i, (name, from_unit, to_unit) in enumerate(favorites, 1):
            favorites_text += f"{i}. *{name}*\n   {from_unit} → {to_unit}\n\n"
        
        keyboard = []
        for favorite in favorites:
            keyboard.append([f"⭐ {favorite[0]}"])
        keyboard.append(["❌ Удалить избранное", "🔙 Главное меню"])
        
        await update.message.reply_text(
            favorites_text,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode='Markdown'
        )
    
    async def show_popular_conversions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать популярные конвертации"""
        await update.message.reply_text(
            "🚀 *Выберите быструю конвертацию:*\n\n"
            "Эти конвертации часто используются и доступны в один клик!",
            reply_markup=self.keyboard.create_popular_conversions_keyboard(),
            parse_mode='Markdown'
        )
    
    async def handle_popular_conversion(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка выбора популярной конвертации"""
        conversion_name = update.message.text
        
        if conversion_name == "🔙 Назад":
            await update.message.reply_text(
                "Главное меню:",
                reply_markup=self.keyboard.create_main_keyboard()
            )
            return
        
        if conversion_name in POPULAR_CONVERSIONS:
            from_unit, to_unit = POPULAR_CONVERSIONS[conversion_name]
            
            # Извлекаем значение из строки (например, "10 дюйм (in)" -> 10)
            value_match = re.match(r'([\d.]+)', from_unit)
            if value_match:
                value = float(value_match.group(1))
                from_unit_clean = from_unit.replace(value_match.group(1), '').strip()
                
                # Находим категории для единиц
                from_category = self._find_unit_category(from_unit_clean)
                to_category = self._find_unit_category(to_unit)
                
                try:
                    # Выполняем конвертацию
                    if from_category == "Температура" or to_category == "Температура":
                        result = self.converter.convert_temperature(value, from_unit_clean, to_unit)
                    else:
                        result = self.converter.convert_standard(value, from_unit_clean, to_unit, from_category)
                    
                    result_str = self.converter.format_result(result)
                    
                    await update.message.reply_text(
                        f"🚀 *Результат быстрой конвертации:*\n\n"
                        f"```\n{value} {from_unit_clean} = {result_str} {to_unit}\n```\n"
                        f"Для точной настройки используйте обычную конвертацию",
                        reply_markup=self.keyboard.create_main_keyboard(),
                        parse_mode='Markdown'
                    )
                    
                except Exception as e:
                    await update.message.reply_text(
                        f"❌ Ошибка при конвертации: {str(e)}",
                        reply_markup=self.keyboard.create_main_keyboard()
                    )
    
    async def show_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать историю конвертаций"""
        user_id = update.effective_user.id
        recent_conversions = self.db.get_recent_conversions(user_id)
        
        if not recent_conversions:
            await update.message.reply_text(
                "📈 У вас пока нет истории конвертаций.\n\n"
                "Выполните первую конвертацию, и она появится здесь!",
                reply_markup=self.keyboard.create_main_keyboard()
            )
            return
        
        history_text = "📈 *Последние конвертации:*\n\n"
        for i, (from_value, from_unit, to_value, to_unit, converted_at) in enumerate(recent_conversions, 1):
            date_str = datetime.strptime(converted_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m %H:%M')
            history_text += f"*{i}. {date_str}*\n"
            history_text += f"   {from_value} {from_unit} → {to_value:.4g} {to_unit}\n\n"
        
        stats = self.db.get_user_stats(user_id)
        if stats:
            history_text += f"📊 *Всего конвертаций:* {stats['conversions_count']}"
        
        await update.message.reply_text(
            history_text,
            reply_markup=self.keyboard.create_main_keyboard(),
            parse_mode='Markdown'
        )
    
    async def show_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать статистику пользователя"""
        user_id = update.effective_user.id
        stats = self.db.get_user_stats(user_id)
        
        if not stats:
            await update.message.reply_text(
                "📊 У вас пока нет статистики.\n\n"
                "Выполните первую конвертацию!",
                reply_markup=self.keyboard.create_main_keyboard()
            )
            return
        
        last_active = datetime.strptime(stats['last_activity'], '%Y-%m-%d %H:%M:%S')
        days_ago = (datetime.now() - last_active).days
        
        stats_text = (
            f"📊 *Ваша статистика:*\n\n"
            f"• *Всего конвертаций:* {stats['conversions_count']}\n"
            f"• *Последняя активность:* {days_ago} дней назад\n"
            f"• *Поддерживаемых единиц:* {sum(len(units) for units in PHYSICAL_QUANTITIES.values())}\n"
            f"• *Доступных категорий:* {len(PHYSICAL_QUANTITIES)}"
        )
        
        await update.message.reply_text(
            stats_text,
            reply_markup=self.keyboard.create_main_keyboard(),
            parse_mode='Markdown'
        )
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка текстовых сообщений"""
        text = update.message.text
        
        if text == "📊 Конвертировать":
            await self.convert_start(update, context)
        elif text == "⭐ Избранное":
            await self.show_favorites(update, context)
        elif text == "🚀 Быстрые конвертации":
            await self.show_popular_conversions(update, context)
        elif text == "📈 История":
            await self.show_history(update, context)
        elif text == "📚 Категории":
            await self.show_categories(update, context)
        elif text == "ℹ️ Помощь":
            await self.help_command(update, context)
        else:
            await update.message.reply_text(
                "🤖 Используйте кнопки ниже для навигации или команду /help для справки",
                reply_markup=self.keyboard.create_main_keyboard()
            )
    
    def _get_compatible_units(self, from_category: str) -> Dict:
        """Получить все совместимые единицы для данной категории"""
        compatible_categories = COMPATIBLE_CATEGORIES.get(from_category, [])
        all_units = {}
        
        for category in compatible_categories:
            all_units.update(PHYSICAL_QUANTITIES.get(category, {}))
        
        return all_units
    
    def _find_unit_category(self, unit: str) -> str:
        """Найти категорию для единицы измерения"""
        for category, units in PHYSICAL_QUANTITIES.items():
            if unit in units:
                return category
        return "Неизвестно"
    
    def _get_conversion_hint(self, from_unit: str, to_unit: str) -> str:
        """Получить подсказку для конвертации"""
        hints = {
            ("верста", "километр (км)"): "💡 1 верста = 1.0668 км",
            ("сажень", "метр (м)"): "💡 1 сажень = 2.1336 м",
            ("аршин", "метр (м)"): "💡 1 аршин = 0.7112 м",
            ("дюйм (in)", "сантиметр (см)"): "💡 1 дюйм = 2.54 см",
            ("фут (ft)", "метр (м)"): "💡 1 фут = 0.3048 м",
            ("Фаренгейт (°F)", "Цельсий (°C)"): "💡 32°F = 0°C, 212°F = 100°C",
            ("байт (byte)", "бит (bit)"): "💡 1 байт = 8 бит",
            ("мегабит/сек (Mbps)", "мегабайт/сек (MBps)"): "💡 100 Мбит/с = 12.5 МБ/с"
        }
        
        for (from_u, to_u), hint in hints.items():
            if from_u in from_unit and to_u in to_unit:
                return hint
        
        return "💡 Введите значение для конвертации"

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.message:
        await update.message.reply_text(
            "❌ Произошла непредвиденная ошибка. Пожалуйста, попробуйте снова.\n"
            "Если ошибка повторяется, используйте /help для получения справки.",
            reply_markup=KeyboardManager().create_main_keyboard()
        )

async def post_init(application: Application) -> None:
    """Функция, выполняемая после инициализации бота"""
    logger.info("🤖 Бот успешно инициализирован и готов к работе!")
    logger.info(f"📊 Загружено {len(PHYSICAL_QUANTITIES)} категорий с {sum(len(units) for units in PHYSICAL_QUANTITIES.values())} единицами измерения")

def main() -> None:
    """Запуск бота"""
    # Инициализация базы данных
    init_database()
    
    # Создаем приложение
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    
    # Инициализируем обработчики
    handlers = BotHandlers()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("categories", handlers.show_categories))
    application.add_handler(CommandHandler("favorites", handlers.show_favorites))
    application.add_handler(CommandHandler("history", handlers.show_history))
    application.add_handler(CommandHandler("stats", handlers.show_stats))
    
    # ConversationHandler для конвертации
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("convert", handlers.convert_start),
            MessageHandler(filters.Text(["📊 Конвертировать"]), handlers.convert_start)
        ],
        states={
            SELECT_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.select_category)],
            SELECT_UNIT_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.select_unit_from)],
            SELECT_UNIT_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.select_unit_to)],
            ENTER_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.enter_value)],
            SAVE_FAVORITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.save_favorite_handler)],
        },
        fallbacks=[CommandHandler("cancel", handlers.start)],
    )
    
    application.add_handler(conv_handler)
    
    # Обработчики для быстрых конвертаций
    application.add_handler(MessageHandler(
        filters.Text(["🚀 Быстрые конвертации"]), 
        handlers.show_popular_conversions
    ))
    application.add_handler(MessageHandler(
        filters.Text([name for name in POPULAR_CONVERSIONS.keys()]), 
        handlers.handle_popular_conversion
    ))
    
    # Обработчик текстовых сообщений (для кнопок)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handlers.handle_text_message
    ))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logger.info("🚀 Запускаю бота...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
