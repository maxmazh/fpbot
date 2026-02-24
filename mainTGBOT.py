import asyncio
import logging
from openai import AsyncOpenAI
from os import getenv
from dotenv import load_dotenv
from datetime import datetime, date, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from norms import * #MALE_NORMS, FEMALE_NORMS, SHUTTLE_RUN_10x10_FEMALE, SHUTTLE_RUN_10x10_MALE, get_exercise_points, get_minimum_points, get_shuttle_run_points
from aiogram.enums import ParseMode
import aiohttp
import json
import os
from Promt import SYSTEM_PROMPT
# from aiogram.utils.keyboard import ReplyKeyboardBuilder
# from Command.router import router

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен бота
load_dotenv()
BOT_TOKEN = getenv("TOKEN")
YANDEX_FOLDER_ID = getenv("YANDEX_FOLDER_ID")
YANDEX_API_KEY = getenv("YANDEX_API_KEY")

YANDEXGPT_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# Инициализация диспетчера и роутера
dp = Dispatcher(storage=MemoryStorage())
# dp.include_router(router)

# Состояния FSM
class UserState(StatesGroup):
    gender = State()
    age = State()
    service = State()
    choice_for_justice = State()  # новое состояние для выбора норматива юстиции
    exercise_type = State()
    exercise_count = State()
    shuttle_run = State()

# Класс для хранения данных пользователя
class UserData:
    def __init__(self):
        self.gender = None
        self.age = None
        self.service = None
        self.exercise_type = None
        self.exercise_count = None
        self.shuttle_run_time = None

class YandexGPTStates(StatesGroup):
    waiting_for_question = State()

class PersistentRateLimiter:
    def __init__(self, max_requests=100, storage_file="rate_limit.json"):
        self.max_requests = max_requests
        self.storage_file = storage_file
        self.load_data()
    
    def load_data(self):
        """Загружает данные из файла"""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    data = json.load(f)
                    self.requests_today = data.get('requests', 0)
                    saved_date = datetime.fromisoformat(data.get('date')).date()
                    
                    # Если файл вчерашний, обнуляем счетчик
                    if saved_date != date.today():
                        self.requests_today = 0
            except:
                self.requests_today = 0
        else:
            self.requests_today = 0
    
    def save_data(self):
        """Сохраняет данные в файл"""
        data = {
            'requests': self.requests_today,
            'date': datetime.now().isoformat()
        }
        with open(self.storage_file, 'w') as f:
            json.dump(data, f)
    
    async def check_limit(self):
        """Проверяет лимит"""
        # Проверяем лимит
        if self.requests_today >= self.max_requests:
            return False
        
        self.requests_today += 1
        self.save_data()
        return True

# Использование
persistent_limiter = PersistentRateLimiter(max_requests=100)

# Хранилище данных пользователей
user_data_dict = {}

# Главное меню с inline-кнопками
def get_main_menu_keyboard():
    #Возвращает inline-клавиатуру главного меню
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏋️ Физическая подготовка", callback_data="menu_phys"),
                InlineKeyboardButton(text="🔫 Огневая подготовка", callback_data="menu_fire")
            ],
            [
                InlineKeyboardButton(text="📌 Служебная подготовка", callback_data="menu_official"),
                InlineKeyboardButton(text="📖 Нормативная база", callback_data="menu_npa")
            ],
            [
                InlineKeyboardButton(text="❓ Ответы на вопросы", callback_data="menu_faq"),
                InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu_help")
            ],
            [
                InlineKeyboardButton(text="🤖 Умный помощник", callback_data="menu_yandexgpt")
            ]
        ]
    )

def get_yandex_menu():
    #Главное меню YandexGPT
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❓ Задать вопрос", callback_data="yagpt_ask")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
        ]
    )

def get_back_menu():
    #Кнопки после ответа
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❓ Ещё вопрос", callback_data="yagpt_ask")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu_is_yagpt")]
        ]
    )

@dp.callback_query(F.data == "back_to_menu_is_yagpt") # возврат в меню
async def back_to_menu_is_yagpt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "Главное меню. Выберите раздел:",
        reply_markup=get_main_menu_keyboard()
    )

async def ask_yandexgpt(message: types.Message, question: str, state: FSMContext):
    #Отправка запроса к YandexGPT API
    
    # Показываем "печатает..."
    await message.bot.send_chat_action(message.chat.id, "typing")
    
    try:
        # Формируем тело запроса [citation:1][citation:3]
        model_uri = f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite"
        logger.info(f"Model URI: {model_uri}")
        
        payload = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",  # можно использовать yandexgpt или yandexgpt-pro
            "completionOptions": {
                "stream": False,
                "temperature": 0.3,  # Низкая температура = точные ответы
                "maxTokens": "800"
            },
            "messages": [
                {
                    "role": "system",
                    "text": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "text": question
                }
            ]
        }
        
        # Заголовки для аутентификации с API-ключом [citation:3]
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {YANDEX_API_KEY}"  # Используем Api-Key
        }
        
        # Отправляем запрос
        async with aiohttp.ClientSession() as session:
            async with session.post(YANDEXGPT_API_URL, json=payload, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"YandexGPT API error: {response.status} - {error_text}")
                    
                    if response.status == 400:
                        if "folder ID" in error_text:
                            # Извлекаем правильный Folder ID из ошибки
                            import re
                            match = re.search(r"folder ID '([^']+)'", error_text)
                            correct_folder = match.group(1) if match else "неизвестно"
                            
                            user_msg = (
                                f"❌ **Ошибка в настройках бота**\n\n"
                                f"Используется неверный Folder ID.\n"
                                f"**Правильный Folder ID:** `{correct_folder}`\n\n"
                                f"Сообщи разработчику, чтобы исправил."
                            )
                        else:
                            user_msg = f"❌ Ошибка API (код 400)"
                    else:
                        user_msg = f"❌ Ошибка API (код {response.status})"
                    
                    await message.answer(
                        user_msg,
                        reply_markup=get_back_menu()
                    )
                    await state.clear()
                    return
                
                result = await response.json()
                answer = result["result"]["alternatives"][0]["message"]["text"]
        
        # Отправляем ответ пользователю
        await message.answer(
            f"🤖 **YandexGPT (приказ №44):**\n\n{answer}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_menu()
        )
        
        # Очищаем состояние
        await state.clear()
        
    except Exception as e:
        logger.error(f"YandexGPT ошибка: {e}")
        error_message = f"❌ Ошибка при обращении к YandexGPT: {str(e)[:100]}"
        
        # Создаем клавиатуру для возврата
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
            ]
        )
        
        await message.answer(
            error_message,
            reply_markup=keyboard
        )
        await state.clear()

# Вход в меню YandexGPT
@dp.callback_query(F.data == "menu_yandexgpt")
async def yandexgpt_menu(callback: types.CallbackQuery, state: FSMContext):
    #Главное меню YandexGPT помощника
    await callback.answer()
    await state.clear()
    
    text = (
        "🤖 **ПОМОЩНИК НА ОСНОВЕ YandexGPT**\n\n"
        "Я отвечаю на вопросы по приказу МВД №44:\n\n"
        "✅ Нормативы полиции, вн. службы и юстиции\n"
        "✅ Баллы за упражнения\n"
        "✅ Возрастные группы\n"
        "✅ Техника выполнения\n\n"
        "Выбери действие:"
    )
    
    await callback.message.edit_text(
        text, 
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_yandex_menu()
    )

@dp.callback_query(F.data == "back_to_yagpt")
async def back_to_yagpt(callback: types.CallbackQuery, state: FSMContext):
    #Возврат в меню YandexGPT
    await callback.answer()
    await state.clear()
    await yandexgpt_menu(callback, state)

@dp.callback_query(F.data == "yagpt_ask")
async def yagpt_ask(callback: types.CallbackQuery, state: FSMContext):
    #Начать ввод вопроса
    await callback.answer()
    
    await callback.message.answer(
        "❓ **Напиши свой вопрос**\n\n"
        "Например:\n"
        "• Сколько отжиманий нужно полицейскому в 25 лет?\n"
        "• Какие нормативы для юстиции в 30 лет?\n"
        "• Сколько баллов за 10 подтягиваний?\n\n"
        "Чтобы получить точный ответ - обязательно укажи свои данные (пол/возраст/служба)",
        parse_mode=ParseMode.MARKDOWN
    )
    
    await state.set_state(YandexGPTStates.waiting_for_question)

@dp.message(YandexGPTStates.waiting_for_question)
async def handle_yagpt_question(message: types.Message, state: FSMContext):
    #Обработка текстового вопроса
    question = message.text
    
    if not question or len(question.strip()) < 2:
        await message.answer("❓ Пожалуйста, напиши вопрос.")
        return
    
    # Проверяем лимит
    if not await persistent_limiter.check_limit():
        remaining = persistent_limiter.get_remaining()
        await message.answer(
            "⚠️ **Превышен лимит запросов к YandexGPT на сегодня**\n\n"
            f"Доступно: 100 запросов в день\n"
            f"Осталось: {remaining} запросов\n\n"
            "Попробуйте завтра или обратитесь к администратору.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_yandex_menu()
        )
        await state.clear()
        return

    await ask_yandexgpt(message, question, state)


# Обработчики команд
@dp.message(Command("start")) # команда старт
# @dp.message(F.text) любого текста
async def cmd_start(message: types.Message, state: FSMContext):
    user_data_dict[message.from_user.id] = UserData()
    clear_user_data(message.from_user.id)
    await state.clear()

    await message.answer(
        f"👋 Добро пожаловать, {message.from_user.first_name}!\n\n"
        "📝Я бот для помощи сотрудникам МВД👮\n"
        "Выбери нужный раздел:",
        reply_markup=get_main_menu_keyboard()
    )

def get_phys_menu_keyboard():
    """Подменю физической подготовки"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏅 Нормативы по ФП", callback_data="test_phys"),
                InlineKeyboardButton(text="🎥 Боевые приемы борьбы", url="https://xn--c1aqofh.xn--b1aew.xn--p1ai/physical-training-guide/combating")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")
            ]
        ]
    )

@dp.callback_query(F.data == "menu_phys")
async def phys_menu(callback: types.CallbackQuery):
    """Меню физической подготовки"""
    await callback.answer()
    await callback.message.edit_text(
        "🏋️ **ФИЗИЧЕСКАЯ ПОДГОТОВКА**\n\n"
        "<b>Нормативы по ФП</b> - при вводе данных оценивает сдали ли Вы физическую подготовку\n<b>БПБ</b> - переводит на страницу с видео по выполнению приемов борьбы\n\n"
        "Выберите раздел:",
        reply_markup=get_phys_menu_keyboard(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "test_phys") # раздел физическая подготовка
async def start_testing(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    clear_user_data(callback.from_user.id)
    await state.clear()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👨 Мужской", callback_data="gender_male"), 
            InlineKeyboardButton(text="👩 Женский", callback_data="gender_female")],

        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]]
    )
    
    await callback.message.edit_text("Выберите ваш ПОЛ:", reply_markup=keyboard)

@dp.callback_query(F.data == "menu_fire") # раздел огневая подготовка
async def start_fire_training(callback: types.CallbackQuery):
    await callback.answer()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️📚 Тестирование", url="https://onlinetestpad.com/ettoxdagdvnhu"), 
            InlineKeyboardButton(text="🎯 Упражнения", callback_data="fire_exercises")],

        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]]
    )
    
    await callback.message.edit_text("Выберите раздел:", reply_markup=keyboard)

@dp.callback_query(F.data == "fire_exercises") # раздел упражнения огневая подготовка
async def menu_fire_exercises(callback: types.CallbackQuery):
    await callback.answer()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔫Упр №5 из пистолета", callback_data="fire_ex_5"), 
            InlineKeyboardButton(text="💥Упр №7 из автомата", callback_data="fire_ex_7")],

        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]]
    )
    
    await callback.message.edit_text(
        "📋 **КОНТРОЛЬНЫЕ УПРАЖНЕНИЯ ПО ОГНЕВОЙ ПОДГОТОВКЕ**\n\n"
        "Выберите упражнение для просмотра описания:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
        )

@dp.callback_query(F.data == "fire_ex_5")
async def show_exercise_5(callback: types.CallbackQuery):
    """Показать описание упражнения №5 из пистолета"""
    await callback.answer()
    
    exercise_text = (
        "🔫 **УПРАЖНЕНИЕ №5 СТРЕЛЬБ ИЗ ПИСТОЛЕТА**\n\n"
        "**Наименование:** Скоростная стрельба с места по неподвижной цели с заданной зоной поражения\n\n"
        "**Цель:** грудная фигура (мишень N 4с или N 6в), зона поражения - прямоугольник, обозначенный пунктирной линией (белого цвета)\n\n"
        "**Огневой рубеж:** 10 метров\n"
        "**Количество патронов:** 4 штуки\n"
        "**Время на выполнение:** не более 10 секунд\n"
        "**Положение для стрельбы:** стоя\n\n"
        "**Порядок выполнения:**\n"
        "1. По команде «Заряжай!» сотрудник снаряжает магазин, убирает оружие в кобуру и докладывает о готовности\n"
        "2. По команде «Огонь!» сотрудник принимает изготовку к стрельбе и производит 4 прицельных выстрела\n\n"
        "**Критерии оценки:**\n"
        "• «Удовлетворительно» — поражена установленная зона 3 и более пулями (полиция)\n"
        "• «Удовлетворительно» — поражена установленная зона 2 и более пулями (вн. сулжба, юстиция)\n"
        "• «Неудовлетворительно» — поражена менее чем указано выше"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к упражнениям", callback_data="fire_exercises")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
        ]
    )
    
    await callback.message.edit_text(
        exercise_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "fire_ex_7")
async def show_exercise_7(callback: types.CallbackQuery):
    #описание упражнения №7 из автомата"""
    await callback.answer()
    
    exercise_text = (
        "🔫 **УПРАЖНЕНИЕ №7 СТРЕЛЬБ ИЗ АВТОМАТА**\n\n"
        "**Наименование:** Стрельба с места по неподвижной цели в ограниченное время\n\n"
        "**Цель:** специальная поясная фигура (мишень N 2б, или N 2н, или N 2о);\n\n"
        "**Огневой рубеж:** 25 метров\n"
        "**Количество патронов:** 4 штуки\n"
        "**Время на выполнение:** не более 15 секунд\n"
        "**Положение для стрельбы:** стоя\n"
        "**Вид огная:** одиночный\n"
        "**Порядок выполнения:**\n"
        "1. По команде «Заряжай!» сотрудник переводит оружие в положение 'На ремень' и докладывает о готовности к стрельбе\n"
        "2. По команде «Огонь!» сотрудник принимает изготовку к стрельбе и производит 4 прицельных выстрела\n\n"
        "**Критерии оценки:**\n"
        "• «Удовлетворительно» — поражена установленная зона 2 и более пулями\n"
        "• «Неудовлетворительно» — поражена 1 и менее пулями"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к упражнениям", callback_data="fire_exercises")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
        ]
    )
    
    await callback.message.edit_text(
        exercise_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

# Обработчик для ответов на вопросы
@dp.callback_query(F.data == "menu_faq")
async def start_faq(callback: types.CallbackQuery):
    await callback.answer()
    faq_text = (
        "📋 **Часто задаваемые вопросы:**\n\n"
        "1. **Как считать баллы?**\n"
        "   Баллы рассчитываются по таблицам нормативов согласно п. 343.1 (мужчины) и 343.2 (женщины) приказа МВД от 2 февраля 2024 г. №44\n\n"
        "2. **Какие нужны минимальные баллы?**\n"
        "   Необходимо набрать сумму баллов по двум упражнениям согласно возрастной группе (полиция)"
        "   Для вн. службы и юстиции за ОДНО упражнение согласно возрастной группе\n\n"
        "3. **Как посчитать сколько баллов нужно набрать?**"
        "   Воспользуйтесь расчетом нормативов по ФП в разделе 'Физическая подготовка'\n\n"
        "4. **Что сдают на итоговых занятиях?**\n"
        "   Сдаются три дисциплины:\n" \
        "   1. Служебная подготовка (тестирование - 20 вопросов)\n"
        "   2. Огневая подготовка (тестирование - 20 вопросов и выполнение упражнения)\n"
        "   3. Физическая подготовка (нормативы и боевые приемы борьбы)\n\n"
        "5. **Сколько попыток для сдачи итоговых занятий?**\n"
        "   Две попытки, после второй не сдачи, сотрудник отстраняется от службы.\n\n"
        "6. **Можно ли пересдать итоговые при получении оценки неудовлетворительно?**\n"
        "   Нужно, но уже после подписания протокола и в установленные дни\n\n"
        "7. **Когда можно сдавать на классность?\n"
        "   В период итоговых занятий в установленные приказом дни."
    )
    
    await callback.message.answer(faq_text)
    await show_main_menu(callback)

@dp.callback_query(F.data == "menu_help") # меню помощь
async def show_help(callback: types.CallbackQuery):
    await callback.answer()
    await cmd_help(callback.message)
    await show_main_menu(callback)

@dp.callback_query(F.data == "menu_npa")
async def start_npa(callback: types.CallbackQuery):
    await callback.answer()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Приказ МВД №44", url="https://mvd.consultant.ru/documents/1058340?items=100&page=1"), 
            InlineKeyboardButton(text="Приказ МВД №626", url="https://mvd.consultant.ru/documents/1058964")],

        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]]
    )
    
    await callback.message.edit_text("Приказ МВД №44 - основной приказ по деятельности ПСиФП\n"
        "Приказ МВД №626 - регламентирует порядок присвоения классности\n"
        "Выберите необходимый Вам ПРИКАЗ", reply_markup=keyboard)

@dp.callback_query(F.data == "menu_official")
async def start_official(callback: types.CallbackQuery):
    await callback.answer()
    official_text = (
        "В скором времени здесь будет тестирование по служебной подготовке и иные материалы\n"
        "**Раздел в разработке**"
    )
    
    await callback.message.answer(official_text)
    await show_main_menu(callback)

@dp.callback_query(F.data == "back_to_menu") # возврат в меню
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    clear_user_data(callback.from_user.id)
    await callback.message.edit_text(
        "Главное меню. Выберите раздел:",
        reply_markup=get_main_menu_keyboard()
    )

async def show_main_menu(message_or_callback): # главное меню
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.answer(
            "Выберите раздел:",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await message_or_callback.answer(
            "Выберите раздел:",
            reply_markup=get_main_menu_keyboard()
        )

@dp.callback_query(F.data.in_(["gender_male", "gender_female"])) # кнопка пол
async def process_gender(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    gender = "мужской" if callback.data == "gender_male" else "женский"
    
    user_data = user_data_dict.get(callback.from_user.id)
    if user_data:
        user_data.gender = gender

    await callback.message.edit_text(
        f"Пол: {gender}\n" 
        "Введите ваш возраст (полных лет) или дату рождения в формате ДД.ММ.ГГГГ:"
    )
    await state.set_state(UserState.age)

@dp.message(UserState.age) # введение возраста
async def process_age(message: types.Message, state: FSMContext):
    age = None
    
    # Проверяем, введен ли возраст числом
    if message.text.isdigit():
        age = int(message.text)
    # Проверяем, введена ли дата рождения
    elif "." in message.text:
        try:
            birth_date = datetime.strptime(message.text, "%d.%m.%Y").date()
            today = date.today()
            age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        except ValueError:
            await message.answer("Неверный формат даты. Введите возраст числом или дату в формате ДД.ММ.ГГГГ")
            return
    else:
        await message.answer("Пожалуйста, введите возраст числом или дату рождения в формате ДД.ММ.ГГГГ")
        return
    
    if not (16 <= age <= 70):
        await message.answer("Возраст должен быть от 16 до 70 лет. Введите корректный возраст:")
        return
    
    user_data = user_data_dict.get(message.from_user.id)
    if user_data:
        user_data.age = age
    
    gender = user_data.gender if user_data else "не указан"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👮 Полиция", callback_data="service_police"),
            InlineKeyboardButton(text="⚖️ Вн. сл, юст", callback_data="service_justice")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_gender")]
        ]
    )
    
    await message.answer(
        f"Пол {gender}\nВозраст: {age} лет\n\nВыберите службу:",
        reply_markup=keyboard)
    await state.set_state(UserState.service)

@dp.callback_query(F.data == "back_to_gender") # возврат к выбору пола
async def back_to_gender(callback: types.CallbackQuery, state: FSMContext):
    """Возврат к выбору пола"""
    await callback.answer()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👨 Мужской", callback_data="gender_male"),
                InlineKeyboardButton(text="👩 Женский", callback_data="gender_female")
            ],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
        ]
    )
    
    await callback.message.edit_text(
        "Выберите ваш пол:",
        reply_markup=keyboard
    )
    await state.set_state(UserState.gender)

@dp.callback_query(F.data.in_(["service_police", "service_justice"])) # кнопка выбора службы
async def process_service(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    service = "полиция" if callback.data == "service_police" else "вн. сл, юст"
    
    user_data = user_data_dict.get(callback.from_user.id)
    if user_data:
        user_data.service = service
    
    if service == "полиция":
        await show_exercise_selection(callback, state)
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💪 Силовое упражнение", callback_data="justice_exercise")],
                [InlineKeyboardButton(text="🏃 Челночный бег", callback_data="justice_shuttle")],
                [InlineKeyboardButton(text="⬅️ Назад к службе", callback_data="back_to_service")]
            ]
        )

        await callback.message.edit_text(
            "Выберите, что будете сдавать (только один норматив):",
            reply_markup=keyboard
        )
        await state.set_state(UserState.choice_for_justice)

@dp.callback_query(F.data == "back_to_age") # возврат к выбору возраста
async def back_to_age(callback: types.CallbackQuery, state: FSMContext):
    """Возврат к вводу возраста"""
    await callback.answer()
    
    user_data = user_data_dict.get(callback.from_user.id)
    age_text = f"{user_data.age} лет" if user_data and user_data.age else ""
    
    await callback.message.edit_text(
        f"Возраст: {age_text}\n\nВведите ваш возраст (полных лет) или дату рождения в формате ДД.ММ.ГГГГ:"
    )
    await state.set_state(UserState.age)
    
@dp.callback_query(F.data.in_(["justice_exercise", "justice_shuttle"])) # кнопка силовое упражнение
async def process_justice_choice(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    user_data = user_data_dict.get(callback.from_user.id)

    if callback.data == "justice_exercise":
        user_data.shuttle_run_time = None
        # Показываем выбор упражнений
        await show_exercise_selection(callback, state)
    else:
        # Для челночного бега
        user_data.exercise_type = None
        user_data.exercise_count = 0
        
        await callback.message.edit_text(
            f"Служба: {user_data.service.upper()}\n\n"
            "Введите время челночного бега 10x10 метров (в секундах, формат: 28.5):"
        )
        await state.set_state(UserState.shuttle_run)

async def show_exercise_selection(callback: types.CallbackQuery, state: FSMContext):
    """Показать выбор упражнений"""
    user_data = user_data_dict.get(callback.from_user.id)
    
    if not user_data:
        await callback.message.answer("Ошибка: данные не найдены. Начните заново /start.")
        return

    if user_data.gender == "мужской":
        exercises = [
            ("🏋️ Отжимания", "exercise_pushups"),
            ("💪 Подтягивания", "exercise_pullups"),
            ("🏅 Гиря 24кг", "exercise_kettlebell")
        ]
    else:
        exercises = [
            ("🏋️ Отжимания", "exercise_pushups"),
            ("💪 Пресс", "exercise_abs")
        ]

    buttons = []
    for text, data in exercises:
        buttons.append([InlineKeyboardButton(text=text, callback_data=data)])
    
    # Добавляем кнопку назад
    if user_data.service == "полиция":
        # Для полиции: назад → выбор службы
        buttons.append([InlineKeyboardButton(text="⬅️ Назад к службе", callback_data="back_to_service")])
    else:
        # Для юстиции: назад → выбор норматива
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_justice_choice")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(
        "Выберите упражнение (силовое):",
        reply_markup=keyboard
    )
    await state.set_state(UserState.exercise_type)

@dp.callback_query(F.data == "back_to_service") # возврат к выбору службы
async def back_to_service(callback: types.CallbackQuery, state: FSMContext):
    """Возврат к выбору службы"""
    await callback.answer()
    
    user_data = user_data_dict.get(callback.from_user.id)
    service = user_data.service if user_data else ""
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👮 Полиция", callback_data="service_police"),
                InlineKeyboardButton(text="⚖️ Вн. сл, юст", callback_data="service_justice")
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_age")]
        ]
    )
    
    await callback.message.edit_text(
        f"Служба: {service}\n\nВыберите службу:",
        reply_markup=keyboard
    )
    await state.set_state(UserState.service)

@dp.callback_query(F.data == "back_to_justice_choice") # возврат к выбору норматива для юстиции
async def back_to_justice_choice(callback: types.CallbackQuery, state: FSMContext):
    """Возврат к выбору норматива для юстиции"""
    await callback.answer()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💪 Силовое упражнение", callback_data="justice_exercise")],
            [InlineKeyboardButton(text="🏃 Челночный бег", callback_data="justice_shuttle")],
            [InlineKeyboardButton(text="⬅️ Назад к службе", callback_data="back_to_service")]
        ]
    )
    
    await callback.message.edit_text(
        "Выберите, что будете сдавать (только один норматив):",
        reply_markup=keyboard
    )
    await state.set_state(UserState.choice_for_justice)

@dp.callback_query(F.data.startswith("exercise_")) # выбор упражнения на силу
async def process_exercise_type(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора упражнения"""
    await callback.answer()
    
    exercise_map = {
        "exercise_pushups": "отжимания",
        "exercise_pullups": "подтягивания",
        "exercise_kettlebell": "гиря",
        "exercise_abs": "пресс"
    }
    
    exercise_type = exercise_map.get(callback.data, "отжимания")
    
    user_data = user_data_dict.get(callback.from_user.id)
    if user_data:
        user_data.exercise_type = exercise_type
    
    await callback.message.edit_text(
        f"Упражнение: {exercise_type}\n\n"
        f"Введите количество выполненных повторений для упражнения '{exercise_type}':"
    )
    await state.set_state(UserState.exercise_count)

@dp.message(UserState.exercise_count) # расчте для юстиции
async def process_exercise_count(message: types.Message, state: FSMContext):
    """Обработка ввода количества повторений"""
    if not message.text.isdigit():
        await message.answer("Пожалуйста, введите количество повторений числом:")
        return
    
    count = int(message.text)
    if count < 0 or count > 200:
        await message.answer("Введите корректное количество повторений (от 0 до 200):")
        return
    
    user_data = user_data_dict.get(message.from_user.id)
    if user_data:
        user_data.exercise_count = count
    
    # Разная логика для полиции и юстиции
    if user_data.service == "полиция":
        await message.answer(
            f"Количество: {count} раз\n\n"
            "Введите время челночного бега 10x10 метров (в секундах, формат: 28.5):"
        )
        await state.set_state(UserState.shuttle_run)
    else:
        # Для юстиции сразу показываем результат
        await calculate_and_show_results(message, state, is_justice=True)

@dp.message(UserState.shuttle_run) # расчет времени и выведение результата для полиции
async def process_shuttle_run(message: types.Message, state: FSMContext):
    """Обработка ввода времени челночного бега"""
    try:
        shuttle_time = float(message.text.replace(",", "."))
        if shuttle_time <= 0 or shuttle_time > 100:
            await message.answer("Введите корректное время (например: 28.5):")
            return
    except ValueError:
        await message.answer("Пожалуйста, введите время числом (например: 28.5):")
        return
    
    user_data = user_data_dict.get(message.from_user.id)
    if user_data:
        user_data.shuttle_run_time = shuttle_time
    
    await calculate_and_show_results(message, state)

async def calculate_and_show_results(message: types.Message, state: FSMContext, is_justice=False):
    """Расчет и показ результатов"""
    user_data = user_data_dict.get(message.from_user.id)
    if not user_data:
        await message.answer("Ошибка: данные не найдены")
        return
    
    # Расчет баллов
    exercise_points = 0
    run_points = 0
    
    if user_data.exercise_type and user_data.exercise_count:
        exercise_points = get_exercise_points(
            user_data.gender,
            user_data.exercise_type,
            user_data.exercise_count
        )
    
    if user_data.shuttle_run_time:
        run_points = get_shuttle_run_points(
            user_data.gender,
            user_data.shuttle_run_time
        )

    total_points = exercise_points + run_points
    min_points = get_minimum_points(user_data.gender, user_data.age, user_data.service)
    
    # Формирование результата
    result_text = (
        f"📊 **РЕЗУЛЬТАТЫ**\n\n"
        f"👤 **ДАННЫЕ СОТРУДНИКА:**\n"
        f"• Пол: {user_data.gender.upper()}\n"
        f"• Возраст: {user_data.age} лет\n"
        f"• Служба: {user_data.service.upper()}\n\n"
        f"🏆 **ВЫПОЛНЕННЫЕ НОРМАТИВЫ:**\n"
    )
    
    if user_data.exercise_type:
        result_text += f"• {user_data.exercise_type.upper()}: {user_data.exercise_count} раз\n"
    if user_data.shuttle_run_time:
        result_text += f"• Челночный бег: {user_data.shuttle_run_time} сек\n"
    
    result_text += (
        f"\n⭐ **НАБРАННЫЕ БАЛЛЫ:**\n"
    )

    if user_data.exercise_type:
        result_text += f"• {user_data.exercise_type}: {exercise_points} баллов\n"
    if user_data.shuttle_run_time:
        result_text += f"• Челночный бег: {run_points} баллов\n"
    
    result_text += f"• **ОБЩАЯ СУММА: {total_points} баллов**\n\n"
    result_text += f"🎯 **МИНИМАЛЬНЫЙ ПОРОГ: {min_points} баллов**\n\n"
    
    if user_data.service == "полиция" or is_justice:
        percentage = round((total_points / min_points) * 100, 1) if min_points > 0 else 0
        result_text += f"📈 **ПРОЦЕНТ ВЫПОЛНЕНИЯ: {percentage}%**\n\n"
    
    if total_points >= min_points:
        result_text += "✅ **СОТРУДНИК СДАЛ ФИЗИЧЕСКУЮ ПОДГОТОВКУ!**\n\n"
        result_text += "Поздравляем с успешной сдачей нормативов!"
    else:
        result_text += "❌ **СОТРУДНИК НЕ СДАЛ ФИЗИЧЕСКУЮ ПОДГОТОВКУ!**\n\n"
        result_text += f"Необходимо набрать еще {min_points - total_points} балла/ов."
    
    await message.answer(result_text)

    # Кнопка для возврата в меню
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_menu")],
            [InlineKeyboardButton(text="🔄 Повторное тестирование", callback_data="test_phys")]
        ]
    )
    
    await message.answer("Что дальше?", reply_markup=keyboard)
    if state:
        await state.clear()

@dp.message(Command("help"))  # кнопка ПОМОЩЬ
async def cmd_help(message: types.Message):
    help_text = (
        "📋 **ПОМОЩЬ ПО БОТУ:**\n\n"
        "Бот предназначен для помощи в работе по профессиональной служебной и физической подготовки.\n\n"
        "**Для начала работы с ботом выберите раздел**\n"
        "  - В каждом разделе вы найдете необходимую информацию\n"
        "  - Чтобы получать полные и верные ответы, следуй подсказкам\n"
        "  - Если не удается найти - выбирай умного помощника\n"
        "  - Ответы на многие вопросы есть в разделе 'Ответы на вопросы'\n\n"
        "Если ты СПОРТСМЕН и хочешь присоединится к команде нажимай на кнопку ➡️/sportsmen⬅️\n\n"
        "<b>**Основные команды:**</b>\n"
        "/start - стартовое меню\n"
        "/menu - главное меню\n"
        "/help - показать эту справку\n\n"
        "Для начала выберите раздел ниже или нажмите на /start\n\n"
        f'Остались вопросы или есть интересные идеи - свяжись с автором <a href="https://t.me/MaxMazh">MaxMazh</a>'
    )
    await message.answer(help_text, parse_mode="HTML")

@dp.message(Command("sportsmen"))  # кнопка спортсмен
async def cmd_sportsmen(message: types.Message):
    sport_text = (
        "💥 Если ты активно занимаешься спортом🏃‍♂️🏆\n"
        "👍 Хочешь совершенствовать свои навыки ✨🔥\n\n"
        f"**📩Пиши мне в личные сообщения <a href='https://t.me/MaxMazh'>MaxMazh</a>**\n"
        "Обязательно укажи:\n"
        "1. Каким видом спорта увлекаешься\занимаешься\n"
        "2. Есть ли спортивный разряд\n"
        "3. На какой должности и отделе сейчас работаешь\n"
    )
    await message.answer(sport_text, parse_mode="HTML")
    await message.answer("Выберите раздел:", reply_markup=get_main_menu_keyboard())

@dp.message(Command("menu")) # кнопка МЕНЮ
async def cmd_menu(message: types.Message, state: FSMContext):
    """Команда меню"""
    await state.clear()
    await cmd_start(message, state)

def clear_user_data(user_id): #  Очищает данные пользователя для нового теста
    if user_id in user_data_dict:
        # Создаем новый объект UserData вместо изменения старого
        user_data_dict[user_id] = UserData()

#Обработчик любого текста (показывает меню)
@dp.message()
async def handle_any_message(message: types.Message, state: FSMContext):
    """Обработчик любого сообщения"""
    # Если не команда и не в состоянии FSM, показываем меню
    current_state = await state.get_state()
    if not current_state and not message.text.startswith('/'):
        await message.answer(
            f"{message.from_user.first_name}, выберите действие из меню выше, либо нажминте на команду /start"
        )

# Запуск бота
async def main():
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())