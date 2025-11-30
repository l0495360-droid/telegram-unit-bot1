import os
import logging
import re
import math
import json
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import sqlite3
from contextlib import contextmanager
from enum import Enum
import aiohttp
from dataclasses import dataclass

from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    InputFile
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, 
    ConversationHandler, CallbackQueryHandler, JobQueue
)
from telegram.constants import ParseMode

# Настройка расширенного логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('converter_bot_advanced.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Получаем токен из переменных окружения
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не найден!")
    exit(1)

logger.info("✅ Токен успешно получен")

# Состояния для ConversationHandler
class BotState(Enum):
    SELECT_CATEGORY = 1
    SELECT_UNIT_FROM = 2
    SELECT_UNIT_TO = 3
    ENTER_VALUE = 4
    SAVE_FAVORITE = 5
    ENTER_FAVORITE_NAME = 6
    BATCH_CONVERSION = 7

# Конфигурация бота
class BotConfig:
    MAX_FAVORITES = 50
    MAX_HISTORY = 100
    CACHE_DURATION = 3600  # 1 час
    SESSION_TIMEOUT = 300  # 5 минут
    RATE_LIMIT = 10  # сообщений в минуту

@dataclass
class ConversionResult:
    value: float
    unit_from: str
    unit_to: str
    result: float
    category: str
    timestamp: datetime

class EnhancedUnitConverter:
    """Усовершенствованный конвертер с поддержкой формул и сложных преобразований"""
    
    # Расширенная база единиц измерения
    PHYSICAL_QUANTITIES = {
        "Длина": {
            "метр (м)": {"factor": 1.0, "type": "linear"},
            "километр (км)": {"factor": 1000.0, "type": "linear"},
            "сантиметр (см)": {"factor": 0.01, "type": "linear"},
            "миллиметр (мм)": {"factor": 0.001, "type": "linear"},
            "микрометр (мкм)": {"factor": 1e-6, "type": "linear"},
            "нанометр (нм)": {"factor": 1e-9, "type": "linear"},
            "дюйм (in)": {"factor": 0.0254, "type": "linear"},
            "фут (ft)": {"factor": 0.3048, "type": "linear"},
            "ярд (yd)": {"factor": 0.9144, "type": "linear"},
            "миля (mi)": {"factor": 1609.34, "type": "linear"},
            "морская миля": {"factor": 1852.0, "type": "linear"},
            "астрономическая единица (а.е.)": {"factor": 1.496e11, "type": "linear"},
            "световой год (ly)": {"factor": 9.461e15, "type": "linear"},
            "парсек (pc)": {"factor": 3.086e16, "type": "linear"}
        },
        "Древнерусские меры длины": {
            "вершок": {"factor": 0.04445, "type": "linear"},
            "пядь": {"factor": 0.1778, "type": "linear"},
            "локоть": {"factor": 0.4572, "type": "linear"},
            "аршин": {"factor": 0.7112, "type": "linear"},
            "сажень": {"factor": 2.1336, "type": "linear"},
            "верста": {"factor": 1066.8, "type": "linear"},
            "поприще": {"factor": 1500.0, "type": "linear"}
        },
        "Масса": {
            "килограмм (кг)": {"factor": 1.0, "type": "linear"},
            "грамм (г)": {"factor": 0.001, "type": "linear"},
            "миллиграмм (мг)": {"factor": 1e-6, "type": "linear"},
            "тонна (т)": {"factor": 1000.0, "type": "linear"},
            "центнер (ц)": {"factor": 100.0, "type": "linear"},
            "карат": {"factor": 0.0002, "type": "linear"},
            "фунт (lb)": {"factor": 0.453592, "type": "linear"},
            "унция (oz)": {"factor": 0.0283495, "type": "linear"},
            "пуд": {"factor": 16.3805, "type": "linear"},
            "золотник": {"factor": 0.004266, "type": "linear"},
            "берковец": {"factor": 163.805, "type": "linear"}
        },
        "Время": {
            "секунда (с)": {"factor": 1.0, "type": "linear"},
            "миллисекунда (мс)": {"factor": 0.001, "type": "linear"},
            "микросекунда (мкс)": {"factor": 1e-6, "type": "linear"},
            "минута (мин)": {"factor": 60.0, "type": "linear"},
            "час (ч)": {"factor": 3600.0, "type": "linear"},
            "день": {"factor": 86400.0, "type": "linear"},
            "неделя": {"factor": 604800.0, "type": "linear"},
            "месяц (30 дней)": {"factor": 2592000.0, "type": "linear"},
            "год (365 дней)": {"factor": 31536000.0, "type": "linear"},
            "век": {"factor": 3.15576e9, "type": "linear"}
        },
        "Температура": {
            "Цельсий (°C)": {"type": "temperature"},
            "Фаренгейт (°F)": {"type": "temperature"},
            "Кельвин (K)": {"type": "temperature"},
            "Ранкин (°R)": {"type": "temperature"},
            "Реомюр (°Ré)": {"type": "temperature"}
        },
        "Площадь": {
            "кв. метр (м²)": {"factor": 1.0, "type": "area"},
            "кв. километр (км²)": {"factor": 1e6, "type": "area"},
            "кв. сантиметр (см²)": {"factor": 1e-4, "type": "area"},
            "кв. миллиметр (мм²)": {"factor": 1e-6, "type": "area"},
            "гектар (га)": {"factor": 10000.0, "type": "area"},
            "акр": {"factor": 4046.86, "type": "area"},
            "сотка (ар)": {"factor": 100.0, "type": "area"},
            "кв. дюйм": {"factor": 0.00064516, "type": "area"},
            "кв. фут": {"factor": 0.092903, "type": "area"},
            "кв. миля": {"factor": 2.59e6, "type": "area"},
            "десятина": {"factor": 10925.0, "type": "area"}
        },
        "Объем": {
            "куб. метр (м³)": {"factor": 1.0, "type": "volume"},
            "литр (л)": {"factor": 0.001, "type": "volume"},
            "миллилитр (мл)": {"factor": 1e-6, "type": "volume"},
            "куб. сантиметр (см³)": {"factor": 1e-6, "type": "volume"},
            "куб. дециметр (дм³)": {"factor": 0.001, "type": "volume"},
            "галлон US": {"factor": 0.00378541, "type": "volume"},
            "галлон UK": {"factor": 0.00454609, "type": "volume"},
            "баррель нефтяной": {"factor": 0.158987, "type": "volume"},
            "куб. дюйм": {"factor": 1.6387e-5, "type": "volume"},
            "куб. фут": {"factor": 0.0283168, "type": "volume"},
            "ведро": {"factor": 0.012, "type": "volume"},
            "бочка": {"factor": 0.491976, "type": "volume"},
            "штоф": {"factor": 0.00123, "type": "volume"}
        },
        "Скорость": {
            "метр/сек (м/с)": {"factor": 1.0, "type": "linear"},
            "километр/час (км/ч)": {"factor": 0.277778, "type": "linear"},
            "миля/час (mph)": {"factor": 0.44704, "type": "linear"},
            "узел (kn)": {"factor": 0.514444, "type": "linear"},
            "фут/сек (ft/s)": {"factor": 0.3048, "type": "linear"},
            "скорость света (c)": {"factor": 299792458, "type": "linear"},
            "маховое число (M)": {"factor": 340.3, "type": "linear"}
        },
        "Ускорение": {
            "метр/сек² (м/с²)": {"factor": 1.0, "type": "linear"},
            "фут/сек² (ft/s²)": {"factor": 0.3048, "type": "linear"},
            "g (ускорение свободного падения)": {"factor": 9.80665, "type": "linear"},
            "Гал (Gal)": {"factor": 0.01, "type": "linear"}
        },
        "Давление": {
            "паскаль (Па)": {"factor": 1.0, "type": "linear"},
            "килопаскаль (кПа)": {"factor": 1000.0, "type": "linear"},
            "мегапаскаль (МПа)": {"factor": 1e6, "type": "linear"},
            "бар": {"factor": 1e5, "type": "linear"},
            "миллибар (мбар)": {"factor": 100.0, "type": "linear"},
            "атмосфера (атм)": {"factor": 101325.0, "type": "linear"},
            "мм рт. ст. (торр)": {"factor": 133.322, "type": "linear"},
            "psi": {"factor": 6894.76, "type": "linear"},
            "техническая атмосфера (ат)": {"factor": 98066.5, "type": "linear"}
        },
        "Энергия": {
            "джоуль (Дж)": {"factor": 1.0, "type": "linear"},
            "килоджоуль (кДж)": {"factor": 1000.0, "type": "linear"},
            "мегаджоуль (МДж)": {"factor": 1e6, "type": "linear"},
            "калория (кал)": {"factor": 4.184, "type": "linear"},
            "килокалория (ккал)": {"factor": 4184.0, "type": "linear"},
            "ватт-час (Вт·ч)": {"factor": 3600.0, "type": "linear"},
            "киловатт-час (кВт·ч)": {"factor": 3.6e6, "type": "linear"},
            "электронвольт (эВ)": {"factor": 1.602e-19, "type": "linear"},
            "мегаэлектронвольт (МэВ)": {"factor": 1.602e-13, "type": "linear"},
            "БТЕ (BTU)": {"factor": 1055.06, "type": "linear"},
            "эрг": {"factor": 1e-7, "type": "linear"}
        },
        "Мощность": {
            "ватт (Вт)": {"factor": 1.0, "type": "linear"},
            "киловатт (кВт)": {"factor": 1000.0, "type": "linear"},
            "мегаватт (МВт)": {"factor": 1e6, "type": "linear"},
            "лошадиная сила (л.с.)": {"factor": 735.499, "type": "linear"},
            "лошадиная сила (hp)": {"factor": 745.7, "type": "linear"},
            "калория/сек": {"factor": 4.184, "type": "linear"}
        },
        "Информация": {
            "бит (bit)": {"factor": 1.0, "type": "digital"},
            "байт (byte)": {"factor": 8.0, "type": "digital"},
            "килобит (Kbit)": {"factor": 1024.0, "type": "digital"},
            "килобайт (KB)": {"factor": 8192.0, "type": "digital"},
            "мегабит (Mbit)": {"factor": 1048576.0, "type": "digital"},
            "мегабайт (MB)": {"factor": 8388608.0, "type": "digital"},
            "гигабит (Gbit)": {"factor": 1073741824.0, "type": "digital"},
            "гигабайт (GB)": {"factor": 8589934592.0, "type": "digital"},
            "терабит (Tbit)": {"factor": 1099511627776.0, "type": "digital"},
            "терабайт (TB)": {"factor": 8796093022208.0, "type": "digital"},
            "петабит (Pbit)": {"factor": 1125899906842624.0, "type": "digital"},
            "петабайт (PB)": {"factor": 9007199254740992.0, "type": "digital"}
        },
        "Скорость передачи данных": {
            "бит/сек (bps)": {"factor": 1.0, "type": "digital"},
            "килобит/сек (Kbps)": {"factor": 1024.0, "type": "digital"},
            "мегабит/сек (Mbps)": {"factor": 1048576.0, "type": "digital"},
            "гигабит/сек (Gbps)": {"factor": 1073741824.0, "type": "digital"},
            "терабит/сек (Tbps)": {"factor": 1099511627776.0, "type": "digital"},
            "байт/сек (Bps)": {"factor": 8.0, "type": "digital"},
            "килобайт/сек (KBps)": {"factor": 8192.0, "type": "digital"},
            "мегабайт/сек (MBps)": {"factor": 8388608.0, "type": "digital"},
            "гигабайт/сек (GBps)": {"factor": 8589934592.0, "type": "digital"},
            "терабайт/сек (TBps)": {"factor": 8796093022208.0, "type": "digital"}
        },
        "Углы": {
            "радиан (rad)": {"factor": 1.0, "type": "angle"},
            "градус (°)": {"factor": 0.0174533, "type": "angle"},
            "минута угловая (′)": {"factor": 0.000290888, "type": "angle"},
            "секунда угловая (″)": {"factor": 4.84814e-6, "type": "angle"},
            "оборот (rev)": {"factor": 6.28319, "type": "angle"},
            "град (gon)": {"factor": 0.015708, "type": "angle"}
        },
        "Частота": {
            "герц (Гц)": {"factor": 1.0, "type": "linear"},
            "килогерц (кГц)": {"factor": 1000.0, "type": "linear"},
            "мегагерц (МГц)": {"factor": 1e6, "type": "linear"},
            "гигагерц (ГГц)": {"factor": 1e9, "type": "linear"},
            "оборот/мин (rpm)": {"factor": 0.0166667, "type": "linear"},
            "радиан/сек (rad/s)": {"factor": 0.159155, "type": "linear"}
        },
        "Сила": {
            "ньютон (Н)": {"factor": 1.0, "type": "linear"},
            "килоньютон (кН)": {"factor": 1000.0, "type": "linear"},
            "дина": {"factor": 1e-5, "type": "linear"},
            "килограмм-сила (кгс)": {"factor": 9.80665, "type": "linear"},
            "фунт-сила (lbf)": {"factor": 4.44822, "type": "linear"}
        },
        "Плотность": {
            "кг/м³": {"factor": 1.0, "type": "linear"},
            "г/см³": {"factor": 1000.0, "type": "linear"},
            "г/л": {"factor": 1.0, "type": "linear"},
            "фунт/куб.фут": {"factor": 16.0185, "type": "linear"},
            "фунт/куб.дюйм": {"factor": 27679.9, "type": "linear"}
        },
        "Вязкость": {
            "паскаль-секунда (Па·с)": {"factor": 1.0, "type": "linear"},
            "сантипуаз (сП)": {"factor": 0.001, "type": "linear"},
            "пуаз (П)": {"factor": 0.1, "type": "linear"}
        }
    }

    # Формулы для сложных преобразований
    FORMULAS = {
        "температура": {
            "Цельсий (°C)": {
                "Фаренгейт (°F)": lambda x: (x * 9/5) + 32,
                "Кельвин (K)": lambda x: x + 273.15,
                "Ранкин (°R)": lambda x: (x + 273.15) * 9/5,
                "Реомюр (°Ré)": lambda x: x * 4/5
            },
            "Фаренгейт (°F)": {
                "Цельсий (°C)": lambda x: (x - 32) * 5/9,
                "Кельвин (K)": lambda x: (x + 459.67) * 5/9,
                "Ранкин (°R)": lambda x: x + 459.67,
                "Реомюр (°Ré)": lambda x: (x - 32) * 4/9
            },
            "Кельвин (K)": {
                "Цельсий (°C)": lambda x: x - 273.15,
                "Фаренгейт (°F)": lambda x: (x * 9/5) - 459.67,
                "Ранкин (°R)": lambda x: x * 9/5,
                "Реомюр (°Ré)": lambda x: (x - 273.15) * 4/5
            }
        }
    }

    @classmethod
    def convert_temperature(cls, value: float, from_unit: str, to_unit: str) -> float:
        """Конвертация температуры с поддержкой всех шкал"""
        if from_unit == to_unit:
            return value
        
        # Проверяем наличие формулы
        for base_unit, conversions in cls.FORMULAS["температура"].items():
            if base_unit in from_unit and to_unit in conversions:
                return conversions[to_unit](value)
        
        # Если нет прямой формулы, используем Цельсий как промежуточную
        if "Цельсий" not in from_unit:
            # Конвертируем в Цельсий
            if "Фаренгейт" in from_unit:
                celsius = (value - 32) * 5/9
            elif "Кельвин" in from_unit:
                celsius = value - 273.15
            elif "Ранкин" in from_unit:
                celsius = (value - 491.67) * 5/9
            elif "Реомюр" in from_unit:
                celsius = value * 5/4
            else:
                raise ValueError(f"Неизвестная единица температуры: {from_unit}")
        else:
            celsius = value
        
        # Конвертируем из Цельсия в целевую единицу
        if "Фаренгейт" in to_unit:
            return (celsius * 9/5) + 32
        elif "Кельвин" in to_unit:
            return celsius + 273.15
        elif "Ранкин" in to_unit:
            return (celsius + 273.15) * 9/5
        elif "Реомюр" in to_unit:
            return celsius * 4/5
        else:
            raise ValueError(f"Неизвестная единица температуры: {to_unit}")

    @classmethod
    def convert_standard(cls, value: float, from_unit: str, to_unit: str, category: str) -> float:
        """Конвертация стандартных величин"""
        if category not in cls.PHYSICAL_QUANTITIES:
            raise ValueError(f"Неизвестная категория: {category}")
        
        units_dict = cls.PHYSICAL_QUANTITIES[category]
        
        if from_unit not in units_dict or to_unit not in units_dict:
            raise ValueError(f"Неизвестные единицы измерения")
        
        from_data = units_dict[from_unit]
        to_data = units_dict[to_unit]
        
        # Для температур используем специальный метод
        if from_data.get("type") == "temperature" or to_data.get("type") == "temperature":
            return cls.convert_temperature(value, from_unit, to_unit)
        
        # Стандартная линейная конвертация
        from_factor = from_data["factor"]
        to_factor = to_data["factor"]
        
        return value * from_factor / to_factor

    @classmethod
    def get_compatible_categories(cls, category: str) -> List[str]:
        """Получить список совместимых категорий"""
        compatible = {
            "Длина": ["Длина", "Древнерусские меры длины"],
            "Древнерусские меры длины": ["Длина", "Древнерусские меры длины"],
        }
        
        # По умолчанию категория совместима только сама с собой
        return compatible.get(category, [category])

    @classmethod
    def get_compatible_units(cls, category: str) -> Dict[str, Any]:
        """Получить все совместимые единицы измерения"""
        compatible_categories = cls.get_compatible_categories(category)
        result = {}
        
        for cat in compatible_categories:
            if cat in cls.PHYSICAL_QUANTITIES:
                result.update(cls.PHYSICAL_QUANTITIES[cat])
        
        return result

    @staticmethod
    def format_result(value: float, precision: int = 8) -> str:
        """Умное форматирование результата"""
        if value == 0:
            return "0"
        
        abs_value = abs(value)
        
        # Для очень больших или очень маленьких чисел используем научную нотацию
        if abs_value < 1e-6 or abs_value > 1e12:
            return f"{value:.{precision}e}".replace('e-0', 'e-').replace('e+0', 'e+')
        
        # Определяем оптимальное количество знаков после запятой
        if abs_value < 0.001:
            decimals = 8
        elif abs_value < 1:
            decimals = 6
        elif abs_value < 1000:
            decimals = 4
        else:
            decimals = 2
        
        formatted = f"{value:.{decimals}f}".rstrip('0').rstrip('.')
        
        # Добавляем разделители тысяч для больших чисел
        if '.' in formatted:
            int_part, dec_part = formatted.split('.')
        else:
            int_part, dec_part = formatted, ""
        
        if len(int_part) > 3:
            int_part = f"{int(int_part):,}".replace(',', ' ')
        
        return f"{int_part}.{dec_part}" if dec_part else int_part

    @staticmethod
    def validate_input(text: str) -> Tuple[bool, Optional[float], Optional[str]]:
        """Расширенная валидация ввода с поддержкой формул"""
        try:
            cleaned = text.strip().replace(',', '.').replace(' ', '')
            
            # Специальные константы
            constants = {
                'pi': math.pi, 'π': math.pi,
                'e': math.e,
                'phi': 1.6180339887, 'φ': 1.6180339887,
                'c': 299792458,  # скорость света
                'g': 9.80665,    # ускорение свободного падения
            }
            
            if cleaned.lower() in constants:
                return True, constants[cleaned.lower()], None
            
            # Поддержка дробей
            if '/' in cleaned:
                parts = cleaned.split('/')
                if len(parts) == 2:
                    numerator = float(parts[0])
                    denominator = float(parts[1])
                    if denominator == 0:
                        return False, None, "❌ Деление на ноль невозможно"
                    return True, numerator / denominator, None
            
            # Поддержка простых математических выражений
            if any(op in cleaned for op in ['+', '-', '*', '^']):
                # Заменяем ^ на ** для возведения в степень
                cleaned = cleaned.replace('^', '**')
                # Безопасное вычисление выражения
                try:
                    result = eval(cleaned, {"__builtins__": None}, {
                        "sin": math.sin, "cos": math.cos, "tan": math.tan,
                        "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
                        "exp": math.exp, "pi": math.pi, "e": math.e
                    })
                    if isinstance(result, (int, float)):
                        return True, float(result), None
                except:
                    pass
            
            # Простое число
            value = float(cleaned)
            
            # Проверка на разумные пределы
            if abs(value) > 1e100:
                return False, None, "❌ Слишком большое число"
            if abs(value) < 1e-100 and value != 0:
                return False, None, "❌ Слишком маленькое число"
            
            return True, value, None
            
        except ValueError:
            return False, None, "❌ Пожалуйста, введите корректное числовое значение\nПример: 10, 15.5, 1/2, -40, 0.25, pi, sin(30), 2^8"

class AdvancedDatabaseManager:
    """Усовершенствованный менеджер базы данных"""
    
    def __init__(self):
        self.init_database()
    
    def init_database(self):
        """Инициализация расширенной базы данных"""
        with self.get_db_connection() as conn:
            # Основные таблицы
            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_favorites (
                    user_id INTEGER,
                    favorite_name TEXT,
                    from_unit TEXT,
                    to_unit TEXT,
                    category TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, favorite_name)
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS conversion_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    from_value REAL,
                    from_unit TEXT,
                    to_value REAL,
                    to_unit TEXT,
                    category TEXT,
                    converted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id INTEGER PRIMARY KEY,
                    conversions_count INTEGER DEFAULT 0,
                    favorites_count INTEGER DEFAULT 0,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT DEFAULT 'ru',
                    precision INTEGER DEFAULT 6,
                    notation TEXT DEFAULT 'auto',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица для кэша курсов валют (если добавим в будущем)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS exchange_rates (
                    base_currency TEXT,
                    target_currency TEXT,
                    rate REAL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (base_currency, target_currency)
                )
            ''')
    
    @contextmanager
    def get_db_connection(self):
        """Контекстный менеджер для подключения к БД"""
        conn = sqlite3.connect('converter_bot_advanced.db', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def save_conversion(self, user_id: int, conversion: ConversionResult):
        """Сохранение конвертации в историю"""
        with self.get_db_connection() as conn:
            conn.execute('''
                INSERT INTO conversion_history 
                (user_id, from_value, from_unit, to_value, to_unit, category)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, conversion.value, conversion.unit_from, 
                  conversion.result, conversion.unit_to, conversion.category))
            
            # Обновляем статистику
            conn.execute('''
                INSERT OR REPLACE INTO user_stats 
                (user_id, conversions_count, last_activity)
                VALUES (?, 
                    COALESCE((SELECT conversions_count FROM user_stats WHERE user_id = ?), 0) + 1,
                    CURRENT_TIMESTAMP)
            ''', (user_id, user_id))
    
    def get_user_favorites(self, user_id: int) -> List[Dict]:
        """Получение избранных конвертаций пользователя"""
        with self.get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT favorite_name, from_unit, to_unit, category 
                FROM user_favorites 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            ''', (user_id,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def save_favorite(self, user_id: int, favorite_name: str, from_unit: str, 
                     to_unit: str, category: str):
        """Сохранение избранной конвертации"""
        with self.get_db_connection() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO user_favorites 
                (user_id, favorite_name, from_unit, to_unit, category)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, favorite_name, from_unit, to_unit, category))
            
            # Обновляем счетчик избранного
            conn.execute('''
                UPDATE user_stats 
                SET favorites_count = (
                    SELECT COUNT(*) FROM user_favorites WHERE user_id = ?
                )
                WHERE user_id = ?
            ''', (user_id, user_id))
    
    def delete_favorite(self, user_id: int, favorite_name: str):
        """Удаление избранной конвертации"""
        with self.get_db_connection() as conn:
            conn.execute('''
                DELETE FROM user_favorites 
                WHERE user_id = ? AND favorite_name = ?
            ''', (user_id, favorite_name))
    
    def is_favorite_name_unique(self, user_id: int, favorite_name: str) -> bool:
        """Проверка уникальности имени избранного"""
        with self.get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT 1 FROM user_favorites 
                WHERE user_id = ? AND favorite_name = ?
            ''', (user_id, favorite_name))
            return cursor.fetchone() is None
    
    def get_user_stats(self, user_id: int) -> Optional[Dict]:
        """Получение статистики пользователя"""
        with self.get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT conversions_count, favorites_count, last_activity, first_seen
                FROM user_stats WHERE user_id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_recent_conversions(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Получение последних конвертаций пользователя"""
        with self.get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT from_value, from_unit, to_value, to_unit, category, converted_at
                FROM conversion_history 
                WHERE user_id = ? 
                ORDER BY converted_at DESC 
                LIMIT ?
            ''', (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_most_used_conversions(self, user_id: int, limit: int = 5) -> List[Dict]:
        """Получение самых частых конвертаций"""
        with self.get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT from_unit, to_unit, COUNT(*) as usage_count
                FROM conversion_history 
                WHERE user_id = ?
                GROUP BY from_unit, to_unit
                ORDER BY usage_count DESC
                LIMIT ?
            ''', (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]
    
    def cleanup_old_history(self, days: int = 30):
        """Очистка старой истории"""
        with self.get_db_connection() as conn:
            conn.execute('''
                DELETE FROM conversion_history 
                WHERE converted_at < datetime('now', ?)
            ''', (f'-{days} days',))

class InteractiveKeyboardManager:
    """Менеджер интерактивных клавиатур"""
    
    @staticmethod
    def create_main_menu() -> ReplyKeyboardMarkup:
        """Главное меню"""
        keyboard = [
            ["🔄 Конвертировать", "⭐ Избранное"],
            ["🚀 Быстрые конвертации", "📊 История и статистика"],
            ["⚙️ Настройки", "ℹ️ Справка"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Выберите действие...")
    
    @staticmethod
    def create_categories_menu() -> ReplyKeyboardMarkup:
        """Меню категорий"""
        categories = list(EnhancedUnitConverter.PHYSICAL_QUANTITIES.keys())
        # Группируем по 2 категории в строке для лучшего отображения
        rows = [categories[i:i+2] for i in range(0, len(categories), 2)]
        rows.append(["🔙 Главное меню"])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True)
    
    @staticmethod
    def create_units_menu(units: List[str], back_text: str = "🔙 Назад") -> ReplyKeyboardMarkup:
        """Меню единиц измерения"""
        rows = [units[i:i+2] for i in range(0, len(units), 2)]
        rows.append([back_text])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True)
    
    @staticmethod
    def create_quick_actions_menu() -> ReplyKeyboardMarkup:
        """Меню быстрых действий"""
        keyboard = [
            ["📏 Дюймы → см", "⚖️ Фунты → кг", "🌡️ °F → °C"],
            ["💻 Мбит → МБ/с", "🛣️ Мили → км", "📐 Футы → метры"],
            ["🔙 Главное меню"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    @staticmethod
    def create_history_menu() -> ReplyKeyboardMarkup:
        """Меню истории и статистики"""
        keyboard = [
            ["📈 Последние конвертации", "📊 Статистика"],
            ["🏆 Частые конвертации", "🗑️ Очистить историю"],
            ["🔙 Главное меню"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    @staticmethod
    def create_settings_menu() -> ReplyKeyboardMarkup:
        """Меню настроек"""
        keyboard = [
            ["🎯 Точность вычислений", "🔤 Формат чисел"],
            ["🗣️ Язык интерфейса", "📱 Тема оформления"],
            ["🔙 Главное меню"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    @staticmethod
    def create_after_conversion_menu() -> ReplyKeyboardMarkup:
        """Меню после конвертации"""
        keyboard = [
            ["⭐ Сохранить в избранное", "🔄 Новая конвертация"],
            ["📊 Еще значения", "🚀 Быстрые конвертации"],
            ["🔙 Главное меню"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    @staticmethod
    def create_favorites_menu() -> ReplyKeyboardMarkup:
        """Меню избранного"""
        keyboard = [
            ["📋 Список избранного", "➕ Добавить в избранное"],
            ["🗑️ Удалить избранное", "📤 Экспорт избранного"],
            ["🔙 Главное меню"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

class AdvancedBotHandlers:
    """Усовершенствованные обработчики бота"""
    
    def __init__(self):
        self.converter = EnhancedUnitConverter()
        self.db = AdvancedDatabaseManager()
        self.keyboard = InteractiveKeyboardManager()
        self.user_sessions = {}  # Кэш сессий пользователей
    
    def get_user_session(self, user_id: int) -> Dict:
        """Получение или создание сессии пользователя"""
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {
                'last_activity': datetime.now(),
                'conversion_count': 0,
                'current_category': None,
                'current_units': None
            }
        return self.user_sessions[user_id]
    
    def update_user_activity(self, user_id: int):
        """Обновление активности пользователя"""
        session = self.get_user_session(user_id)
        session['last_activity'] = datetime.now()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Улучшенный обработчик команды /start"""
        user = update.effective_user
        self.update_user_activity(user.id)
        
        welcome_text = f"""🎉 Добро пожаловать, {user.first_name}!

🤖 *Умный конвертер физических величин* версии 2.0

✨ *Новые возможности:*
• 🔄 Конвертация 200+ единиц в 15+ категориях
• ⭐ Умное избранное с быстрым доступом
• 📊 Подробная статистика и аналитика
• 🚀 Пакетная конвертация нескольких значений
• 🎯 Поддержка математических выражений
• 💾 Экспорт истории и избранного
• ⚙️ Гибкие настройки отображения

📋 *Быстрый старт:*
1. Нажмите `🔄 Конвертировать`
2. Выберите категорию и единицы
3. Введите значение (поддерживаются формулы!)

💡 *Примеры ввода:*
`10`, `15.5`, `1/2`, `sin(30)`, `2^8`, `pi/2`

Начните с кнопки ниже! 👇"""
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=self.keyboard.create_main_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Расширенная справка"""
        help_text = """📚 *Полное руководство пользователя*

*Основные команды:*
/start - Главное меню
/convert - Начать конвертацию  
/favorites - Управление избранным
/history - История конвертаций
/stats - Подробная статистика
/settings - Настройки бота
/help - Эта справка

*🔄 Процесс конвертации:*
1. Выберите категорию измерения
2. Выберите исходную и целевую единицы
3. Введите значение для конвертации

*🔢 Поддерживаемые форматы ввода:*
• Целые числа: `10`, `-5`, `1000`
• Дроби: `1/2`, `3/4`, `15/16`
• Десятичные: `15.5`, `0.25`, `-3.14`
• Научная нотация: `1.23e-5`, `5.67e8`
• Константы: `pi`, `e`, `φ` (фи)
• Формулы: `sin(30)`, `2^8`, `sqrt(16)`, `log(100)`

*🚀 Быстрые конвертации:*
• Дюймы ↔ сантиметры
• Фунты ↔ килограммы
• Фаренгейты ↔ Цельсии
• Мили ↔ километры
• И многое другое!

*💡 Советы:*
• Используйте избранное для частых конвертаций
• Просматривайте историю для повтора операций
• Настройте точность вычислений под ваши нужды"""
        
        await update.message.reply_text(
            help_text,
            reply_markup=self.keyboard.create_main_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def show_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Показать категории для конвертации"""
        self.update_user_activity(update.effective_user.id)
        
        categories_text = "📚 *Выберите категорию измерения:*\n\n"
        for category, units in self.converter.PHYSICAL_QUANTITIES.items():
            units_count = len(units)
            categories_text += f"• *{category}* - {units_count} единиц\n"
        
        await update.message.reply_text(
            categories_text,
            reply_markup=self.keyboard.create_categories_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return BotState.SELECT_CATEGORY.value
    
    async def handle_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка выбора категории"""
        user_id = update.effective_user.id
        category = update.message.text
        self.update_user_activity(user_id)
        
        if category == "🔙 Главное меню":
            await update.message.reply_text(
                "Главное меню:",
                reply_markup=self.keyboard.create_main_menu()
            )
            return ConversationHandler.END
        
        if category not in self.converter.PHYSICAL_QUANTITIES:
            await update.message.reply_text(
                "❌ Пожалуйста, выберите категорию из предложенных вариантов.",
                reply_markup=self.keyboard.create_categories_menu()
            )
            return BotState.SELECT_CATEGORY.value
        
        # Сохраняем выбранную категорию в сессии
        session = self.get_user_session(user_id)
        session['current_category'] = category
        
        units = list(self.converter.PHYSICAL_QUANTITIES[category].keys())
        
        await update.message.reply_text(
            f"📏 *{category}*\n\nВыберите исходную единицу измерения:",
            reply_markup=self.keyboard.create_units_menu(units),
            parse_mode=ParseMode.MARKDOWN
        )
        return BotState.SELECT_UNIT_FROM.value
    
    async def handle_unit_from_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка выбора исходной единицы"""
        user_id = update.effective_user.id
        unit_from = update.message.text
        self.update_user_activity(user_id)
        
        if unit_from == "🔙 Назад":
            await update.message.reply_text(
                "📚 Выберите категорию:",
                reply_markup=self.keyboard.create_categories_menu()
            )
            return BotState.SELECT_CATEGORY.value
        
        session = self.get_user_session(user_id)
        category = session.get('current_category')
        
        if not category or unit_from not in self.converter.PHYSICAL_QUANTITIES.get(category, {}):
            await update.message.reply_text(
                "❌ Пожалуйста, выберите единицу измерения из предложенных вариантов.",
                reply_markup=self.keyboard.create_categories_menu()
            )
            return BotState.SELECT_CATEGORY.value
        
        session['unit_from'] = unit_from
        
        # Получаем совместимые единицы
        compatible_units = self.converter.get_compatible_units(category)
        available_units = [unit for unit in compatible_units.keys() if unit != unit_from]
        
        await update.message.reply_text(
            f"🎯 *Целевая единица*\n\nИз: {unit_from}\n\nВыберите целевую единицу:",
            reply_markup=self.keyboard.create_units_menu(available_units),
            parse_mode=ParseMode.MARKDOWN
        )
        return BotState.SELECT_UNIT_TO.value
    
    async def handle_unit_to_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка выбора целевой единицы"""
        user_id = update.effective_user.id
        unit_to = update.message.text
        self.update_user_activity(user_id)
        
        if unit_to == "🔙 Назад":
            session = self.get_user_session(user_id)
            category = session.get('current_category')
            if category:
                units = list(self.converter.PHYSICAL_QUANTITIES[category].keys())
                await update.message.reply_text(
                    f"📏 Выберите исходную единицу для {category}:",
                    reply_markup=self.keyboard.create_units_menu(units)
                )
                return BotState.SELECT_UNIT_FROM.value
        
        session = self.get_user_session(user_id)
        category = session.get('current_category')
        unit_from = session.get('unit_from')
        
        if not all([category, unit_from]):
            await update.message.reply_text(
                "❌ Сессия устарела. Начните заново.",
                reply_markup=self.keyboard.create_main_menu()
            )
            return ConversationHandler.END
        
        # Проверяем совместимость единиц
        compatible_units = self.converter.get_compatible_units(category)
        if unit_to not in compatible_units:
            await update.message.reply_text(
                "❌ Выбранные единицы несовместимы. Пожалуйста, выберите другую единицу.",
                reply_markup=self.keyboard.create_units_menu(
                    [unit for unit in compatible_units.keys() if unit != unit_from]
                )
            )
            return BotState.SELECT_UNIT_TO.value
        
        session['unit_to'] = unit_to
        
        # Создаем подсказку для пользователя
        hint = self._get_conversion_hint(unit_from, unit_to)
        
        input_text = (
            f"🔢 *Введите значение для конвертации*\n\n"
            f"*Из:* {unit_from}\n"
            f"*В:* {unit_to}\n\n"
            f"{hint}\n"
            f"*Поддерживаемые форматы:*\n"
            f"• Числа: `10`, `15.5`, `-40`\n"
            f"• Дроби: `1/2`, `3/4`\n"
            f"• Формулы: `sin(30)`, `2^8`, `pi/2`\n"
            f"• Константы: `pi`, `e`, `φ`"
        )
        
        await update.message.reply_text(
            input_text,
            reply_markup=ReplyKeyboardMarkup([["🔙 Назад"]], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return BotState.ENTER_VALUE.value
    
    async def handle_value_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка ввода значения и выполнение конвертации"""
        user_id = update.effective_user.id
        value_text = update.message.text
        self.update_user_activity(user_id)
        
        if value_text == "🔙 Назад":
            session = self.get_user_session(user_id)
            category = session.get('current_category')
            unit_from = session.get('unit_from')
            
            if category and unit_from:
                compatible_units = self.converter.get_compatible_units(category)
                available_units = [unit for unit in compatible_units.keys() if unit != unit_from]
                
                await update.message.reply_text(
                    "🎯 Выберите целевую единицу:",
                    reply_markup=self.keyboard.create_units_menu(available_units)
                )
                return BotState.SELECT_UNIT_TO.value
        
        # Валидация ввода
        is_valid, value, error_message = EnhancedUnitConverter.validate_input(value_text)
        
        if not is_valid:
            await update.message.reply_text(error_message)
            return BotState.ENTER_VALUE.value
        
        session = self.get_user_session(user_id)
        category = session.get('current_category')
        unit_from = session.get('unit_from')
        unit_to = session.get('unit_to')
        
        if not all([category, unit_from, unit_to]):
            await update.message.reply_text(
                "❌ Ошибка сессии. Пожалуйста, начните заново.",
                reply_markup=self.keyboard.create_main_menu()
            )
            return ConversationHandler.END
        
        try:
            # Выполняем конвертацию
            if category == "Температура":
                result = self.converter.convert_temperature(value, unit_from, unit_to)
            else:
                result = self.converter.convert_standard(value, unit_from, unit_to, category)
            
            # Проверка на специальные значения
            if math.isinf(result) or math.isnan(result):
                await update.message.reply_text(
                    "❌ Результат конвертации выходит за допустимые пределы",
                    reply_markup=self.keyboard.create_main_menu()
                )
                return ConversationHandler.END
            
            # Форматируем результат
            result_str = self.converter.format_result(result)
            value_str = self.converter.format_result(value)
            
            # Создаем объект результата
            conversion_result = ConversionResult(
                value=value,
                unit_from=unit_from,
                unit_to=unit_to,
                result=result,
                category=category,
                timestamp=datetime.now()
            )
            
            # Сохраняем в базу данных
            self.db.save_conversion(user_id, conversion_result)
            
            # Обновляем сессию
            session['conversion_count'] += 1
            session['last_conversion'] = conversion_result
            
            # Формируем красивый ответ
            response = self._format_conversion_response(conversion_result, value_str, result_str)
            
            await update.message.reply_text(
                response,
                reply_markup=self.keyboard.create_after_conversion_menu(),
                parse_mode=ParseMode.MARKDOWN
            )
            
            return BotState.SAVE_FAVORITE.value
            
        except Exception as e:
            logger.error(f"Ошибка конвертации: {e}")
            await update.message.reply_text(
                f"❌ Ошибка при конвертации: {str(e)}\nПожалуйста, попробуйте снова.",
                reply_markup=self.keyboard.create_main_menu()
            )
            return ConversationHandler.END
    
    async def handle_after_conversion(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка действий после конвертации"""
        user_input = update.message.text
        user_id = update.effective_user.id
        self.update_user_activity(user_id)
        
        if user_input == "🔙 Главное меню":
            await update.message.reply_text(
                "Главное меню:",
                reply_markup=self.keyboard.create_main_menu()
            )
            return ConversationHandler.END
        
        elif user_input == "🔄 Новая конвертация":
            return await self.show_categories(update, context)
        
        elif user_input == "📊 Еще значения":
            session = self.get_user_session(user_id)
            if 'unit_from' in session and 'unit_to' in session:
                await update.message.reply_text(
                    "🔢 Введите следующее значение для конвертации:",
                    reply_markup=ReplyKeyboardMarkup([["🔙 Назад"]], resize_keyboard=True)
                )
                return BotState.ENTER_VALUE.value
        
        elif user_input == "⭐ Сохранить в избранное":
            session = self.get_user_session(user_id)
            if 'last_conversion' in session:
                conversion = session['last_conversion']
                favorite_name = f"{conversion.unit_from} → {conversion.unit_to}"
                
                if not self.db.is_favorite_name_unique(user_id, favorite_name):
                    await update.message.reply_text(
                        f"❌ Конвертация \"{favorite_name}\" уже есть в избранном",
                        reply_markup=self.keyboard.create_main_menu()
                    )
                    return ConversationHandler.END
                
                self.db.save_favorite(
                    user_id, favorite_name, 
                    conversion.unit_from, conversion.unit_to, conversion.category
                )
                
                await update.message.reply_text(
                    f"✅ Конвертация сохранена в избранное как:\n\"{favorite_name}\"",
                    reply_markup=self.keyboard.create_main_menu()
                )
            else:
                await update.message.reply_text(
                    "❌ Нет данных для сохранения",
                    reply_markup=self.keyboard.create_main_menu()
                )
            return ConversationHandler.END
        
        elif user_input == "🚀 Быстрые конвертации":
            await self.show_quick_conversions(update, context)
            return ConversationHandler.END
        
        else:
            await update.message.reply_text(
                "Пожалуйста, используйте кнопки для выбора действия",
                reply_markup=self.keyboard.create_main_menu()
            )
            return ConversationHandler.END
    
    async def show_quick_conversions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать быстрые конвертации"""
        quick_text = """🚀 *Быстрые конвертации*

Выберите один из популярных вариантов для мгновенной конвертации:"""
        
        await update.message.reply_text(
            quick_text,
            reply_markup=self.keyboard.create_quick_actions_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def handle_quick_conversion(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка быстрой конвертации"""
        conversion_type = update.message.text
        self.update_user_activity(update.effective_user.id)
        
        if conversion_type == "🔙 Главное меню":
            await update.message.reply_text(
                "Главное меню:",
                reply_markup=self.keyboard.create_main_menu()
            )
            return
        
        # Предопределенные быстрые конвертации
        quick_conversions = {
            "📏 Дюймы → см": (10, "дюйм (in)", "сантиметр (см)", "Длина"),
            "⚖️ Фунты → кг": (1, "фунт (lb)", "килограмм (кг)", "Масса"),
            "🌡️ °F → °C": (32, "Фаренгейт (°F)", "Цельсий (°C)", "Температура"),
            "💻 Мбит → МБ/с": (100, "мегабит/сек (Mbps)", "мегабайт/сек (MBps)", "Скорость передачи данных"),
            "🛣️ Мили → км": (1, "миля (mi)", "километр (км)", "Длина"),
            "📐 Футы → метры": (6, "фут (ft)", "метр (м)", "Длина")
        }
        
        if conversion_type in quick_conversions:
            value, from_unit, to_unit, category = quick_conversions[conversion_type]
            
            try:
                if category == "Температура":
                    result = self.converter.convert_temperature(value, from_unit, to_unit)
                else:
                    result = self.converter.convert_standard(value, from_unit, to_unit, category)
                
                result_str = self.converter.format_result(result)
                
                # Сохраняем в историю
                conversion_result = ConversionResult(
                    value=value, unit_from=from_unit, unit_to=to_unit,
                    result=result, category=category, timestamp=datetime.now()
                )
                self.db.save_conversion(update.effective_user.id, conversion_result)
                
                await update.message.reply_text(
                    f"🚀 *Результат быстрой конвертации:*\n\n"
                    f"```\n{value} {from_unit} = {result_str} {to_unit}\n```\n"
                    f"Для точной настройки используйте обычную конвертацию",
                    reply_markup=self.keyboard.create_main_menu(),
                    parse_mode=ParseMode.MARKDOWN
                )
                
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Ошибка при конвертации: {str(e)}",
                    reply_markup=self.keyboard.create_main_menu()
                )
    
    async def show_history_and_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать меню истории и статистики"""
        await update.message.reply_text(
            "📊 *История и статистика*\n\n"
            "Выберите раздел для просмотра:",
            reply_markup=self.keyboard.create_history_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def show_recent_conversions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать последние конвертации"""
        user_id = update.effective_user.id
        self.update_user_activity(user_id)
        
        conversions = self.db.get_recent_conversions(user_id, 5)
        
        if not conversions:
            await update.message.reply_text(
                "📈 У вас пока нет истории конвертаций.\n\n"
                "Выполните первую конвертацию, и она появится здесь!",
                reply_markup=self.keyboard.create_history_menu()
            )
            return
        
        history_text = "📈 *Последние конвертации:*\n\n"
        for i, conv in enumerate(conversions, 1):
            date_str = datetime.strptime(conv['converted_at'], '%Y-%m-%d %H:%M:%S').strftime('%d.%m %H:%M')
            from_val = self.converter.format_result(conv['from_value'])
            to_val = self.converter.format_result(conv['to_value'])
            history_text += f"*{i}.* {date_str}\n"
            history_text += f"   `{from_val} {conv['from_unit']} → {to_val} {conv['to_unit']}`\n\n"
        
        await update.message.reply_text(
            history_text,
            reply_markup=self.keyboard.create_history_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def show_user_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать подробную статистику пользователя"""
        user_id = update.effective_user.id
        self.update_user_activity(user_id)
        
        stats = self.db.get_user_stats(user_id)
        
        if not stats:
            await update.message.reply_text(
                "📊 У вас пока нет статистики.\n\n"
                "Выполните первую конвертацию!",
                reply_markup=self.keyboard.create_history_menu()
            )
            return
        
        # Получаем дополнительные данные
        recent_conversions = self.db.get_recent_conversions(user_id, 1)
        most_used = self.db.get_most_used_conversions(user_id, 3)
        
        last_active = datetime.strptime(stats['last_activity'], '%Y-%m-%d %H:%M:%S')
        first_seen = datetime.strptime(stats['first_seen'], '%Y-%m-%d %H:%M:%S')
        days_active = (datetime.now() - first_seen).days
        
        stats_text = (
            f"📊 *Ваша статистика*\n\n"
            f"• Всего конвертаций: *{stats['conversions_count']}*\n"
            f"• Избранных конвертаций: *{stats['favorites_count']}*\n"
            f"• Активность: *{days_active}* дней\n"
            f"• Последняя активность: *{last_active.strftime('%d.%m.%Y %H:%M')}*\n\n"
        )
        
        if most_used:
            stats_text += "*Частые конвертации:*\n"
            for i, conv in enumerate(most_used, 1):
                stats_text += f"{i}. `{conv['from_unit']} → {conv['to_unit']}` - {conv['usage_count']} раз\n"
        
        await update.message.reply_text(
            stats_text,
            reply_markup=self.keyboard.create_history_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def show_favorites_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать меню избранного"""
        await update.message.reply_text(
            "⭐ *Управление избранным*\n\n"
            "Выберите действие:",
            reply_markup=self.keyboard.create_favorites_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def show_favorites_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать список избранных конвертаций"""
        user_id = update.effective_user.id
        self.update_user_activity(user_id)
        
        favorites = self.db.get_user_favorites(user_id)
        
        if not favorites:
            await update.message.reply_text(
                "⭐ У вас пока нет избранных конвертаций.\n\n"
                "Чтобы добавить конвертацию в избранное:\n"
                "1. Выполните обычную конвертацию\n"
                "2. Нажмите кнопку \"⭐ Сохранить в избранное\"",
                reply_markup=self.keyboard.create_favorites_menu()
            )
            return
        
        favorites_text = "⭐ *Ваши избранные конвертации:*\n\n"
        for i, fav in enumerate(favorites, 1):
            favorites_text += f"*{i}.* {fav['favorite_name']}\n"
            favorites_text += f"   `{fav['from_unit']} → {fav['to_unit']}`\n\n"
        
        # Создаем клавиатуру для быстрого доступа к избранному
        keyboard = []
        for favorite in favorites[:5]:  # Показываем первые 5
            keyboard.append([f"⭐ {favorite['favorite_name']}"])
        keyboard.append(["🔙 Главное меню"])
        
        await update.message.reply_text(
            favorites_text,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def handle_favorite_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка выбора избранной конвертации"""
        user_id = update.effective_user.id
        favorite_name = update.message.text[2:]  # Убираем "⭐ "
        self.update_user_activity(user_id)
        
        favorites = self.db.get_user_favorites(user_id)
        selected_favorite = next((f for f in favorites if f['favorite_name'] == favorite_name), None)
        
        if selected_favorite:
            # Сохраняем выбранную конвертацию в сессии
            session = self.get_user_session(user_id)
            session.update({
                'current_category': selected_favorite['category'],
                'unit_from': selected_favorite['from_unit'],
                'unit_to': selected_favorite['to_unit']
            })
            
            await update.message.reply_text(
                f"⭐ *{favorite_name}*\n\n"
                f"Введите значение для конвертации:\n"
                f"`{selected_favorite['from_unit']} → {selected_favorite['to_unit']}`",
                reply_markup=ReplyKeyboardMarkup([["🔙 Назад"]], resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Устанавливаем состояние для ввода значения
            context.user_data['state'] = BotState.ENTER_VALUE.value
        else:
            await update.message.reply_text(
                "❌ Избранная конвертация не найдена",
                reply_markup=self.keyboard.create_favorites_menu()
            )
    
    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать меню настроек"""
        await update.message.reply_text(
            "⚙️ *Настройки бота*\n\n"
            "Настройте параметры отображения и поведения:",
            reply_markup=self.keyboard.create_settings_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    def _get_conversion_hint(self, from_unit: str, to_unit: str) -> str:
        """Получить подсказку для конвертации"""
        hints = {
            ("верста", "километр (км)"): "💡 1 верста ≈ 1.0668 км",
            ("сажень", "метр (м)"): "💡 1 сажень ≈ 2.1336 м",
            ("аршин", "метр (м)"): "💡 1 аршин ≈ 0.7112 м",
            ("дюйм (in)", "сантиметр (см)"): "💡 1 дюйм = 2.54 см",
            ("фут (ft)", "метр (м)"): "💡 1 фут = 0.3048 м",
            ("Фаренгейт (°F)", "Цельсий (°C)"): "💡 32°F = 0°C, 212°F = 100°C",
            ("байт (byte)", "бит (bit)"): "💡 1 байт = 8 бит",
            ("мегабит/сек (Mbps)", "мегабайт/сек (MBps)"): "💡 100 Мбит/с ≈ 12.5 МБ/с",
        }
        
        for (from_u, to_u), hint in hints.items():
            if from_u in from_unit and to_u in to_unit:
                return hint
        
        return "💡 Введите значение для конвертации"
    
    def _format_conversion_response(self, conversion: ConversionResult, value_str: str, result_str: str) -> str:
        """Форматирование ответа с результатом конвертации"""
        # Определяем эмодзи для категории
        category_emojis = {
            "Длина": "📏", "Масса": "⚖️", "Время": "⏰", "Температура": "🌡️",
            "Площадь": "📐", "Объем": "🧪", "Скорость": "🚀", "Давление": "📊",
            "Энергия": "⚡", "Мощность": "💪", "Информация": "💻"
        }
        
        emoji = category_emojis.get(conversion.category, "🔢")
        
        response = (
            f"{emoji} *Результат конвертации*\n\n"
            f"*Исходное значение:* `{value_str} {conversion.unit_from}`\n"
            f"*Результат:* `{result_str} {conversion.unit_to}`\n"
            f"*Категория:* {conversion.category}\n\n"
            f"🕒 {conversion.timestamp.strftime('%H:%M:%S')}"
        )
        
        return response
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка текстовых сообщений для навигации"""
        text = update.message.text
        self.update_user_activity(update.effective_user.id)
        
        navigation_handlers = {
            "🔄 Конвертировать": self.show_categories,
            "⭐ Избранное": self.show_favorites_menu,
            "🚀 Быстрые конвертации": self.show_quick_conversions,
            "📊 История и статистика": self.show_history_and_stats,
            "📈 Последние конвертации": self.show_recent_conversions,
            "📊 Статистика": self.show_user_stats,
            "📋 Список избранного": self.show_favorites_list,
            "⚙️ Настройки": self.show_settings,
            "ℹ️ Справка": self.help_command
        }
        
        if text in navigation_handlers:
            await navigation_handlers[text](update, context)
        elif text.startswith("⭐ "):
            await self.handle_favorite_selection(update, context)
        else:
            await update.message.reply_text(
                "🤖 Используйте кнопки ниже для навигации или команду /help для справки",
                reply_markup=self.keyboard.create_main_menu()
            )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        error_text = (
            "❌ *Произошла непредвиденная ошибка*\n\n"
            "Пожалуйста, попробуйте снова или используйте команду /start для перезагрузки бота."
        )
        
        try:
            await update.effective_message.reply_text(
                error_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InteractiveKeyboardManager().create_main_menu()
            )
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

async def cleanup_task(context: ContextTypes.DEFAULT_TYPE):
    """Задача для очистки устаревших данных"""
    try:
        db = AdvancedDatabaseManager()
        db.cleanup_old_history(30)  # Очищаем историю старше 30 дней
        logger.info("✅ Очистка устаревшей истории выполнена")
    except Exception as e:
        logger.error(f"Ошибка при очистке истории: {e}")

async def post_init(application: Application) -> None:
    """Функция, выполняемая после инициализации бота"""
    logger.info("🤖 Продвинутый бот-конвертер успешно запущен!")
    
    # Запускаем периодические задачи
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_task, interval=86400, first=10)  # Ежедневно
    
    # Статистика при запуске
    total_categories = len(EnhancedUnitConverter.PHYSICAL_QUANTITIES)
    total_units = sum(len(units) for units in EnhancedUnitConverter.PHYSICAL_QUANTITIES.values())
    logger.info(f"📊 Загружено {total_categories} категорий с {total_units} единицами измерения")

def main() -> None:
    """Запуск усовершенствованного бота"""
    # Создаем приложение
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    
    # Инициализируем обработчики
    handlers = AdvancedBotHandlers()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("favorites", handlers.show_favorites_menu))
    application.add_handler(CommandHandler("history", handlers.show_history_and_stats))
    application.add_handler(CommandHandler("stats", handlers.show_user_stats))
    application.add_handler(CommandHandler("settings", handlers.show_settings))
    
    # ConversationHandler для процесса конвертации
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("convert", handlers.show_categories),
            MessageHandler(filters.Text(["🔄 Конвертировать"]), handlers.show_categories)
        ],
        states={
            BotState.SELECT_CATEGORY.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_category_selection)
            ],
            BotState.SELECT_UNIT_FROM.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_unit_from_selection)
            ],
            BotState.SELECT_UNIT_TO.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_unit_to_selection)
            ],
            BotState.ENTER_VALUE.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_value_input)
            ],
            BotState.SAVE_FAVORITE.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_after_conversion)
            ],
        },
        fallbacks=[CommandHandler("cancel", handlers.start)],
    )
    
    application.add_handler(conv_handler)
    
    # Обработчики быстрых конвертаций
    application.add_handler(MessageHandler(
        filters.Text(["🚀 Быстрые конвертации"]), 
        handlers.show_quick_conversions
    ))
    
    quick_conversion_types = [
        "📏 Дюймы → см", "⚖️ Фунты → кг", "🌡️ °F → °C",
        "💻 Мбит → МБ/с", "🛣️ Мили → км", "📐 Футы → метры"
    ]
    application.add_handler(MessageHandler(
        filters.Text(quick_conversion_types), 
        handlers.handle_quick_conversion
    ))
    
    # Обработчики истории и статистики
    application.add_handler(MessageHandler(
        filters.Text(["📈 Последние конвертации"]), 
        handlers.show_recent_conversions
    ))
    application.add_handler(MessageHandler(
        filters.Text(["📊 Статистика"]), 
        handlers.show_user_stats
    ))
    
    # Обработчики избранного
    application.add_handler(MessageHandler(
        filters.Text(["📋 Список избранного"]), 
        handlers.show_favorites_list
    ))
    
    # Основной обработчик текстовых сообщений (навигация)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handlers.handle_text_message
    ))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logger.info("🚀 Запускаю продвинутого бота-конвертера...")
    application.run_polling()

if __name__ == "__main__":
    main()


