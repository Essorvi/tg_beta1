from fastapi import FastAPI, APIRouter, HTTPException, Request, Query
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import requests
import json
import hashlib
import secrets
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
import re

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# API Configuration
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
WEBHOOK_SECRET = os.environ['WEBHOOK_SECRET']
USERSBOX_TOKEN = os.environ['USERSBOX_TOKEN']
USERSBOX_BASE_URL = os.environ['USERSBOX_BASE_URL']
ADMIN_USERNAME = os.environ['ADMIN_USERNAME']
REQUIRED_CHANNEL = os.environ['REQUIRED_CHANNEL']
BOT_USERNAME = os.environ.get('BOT_USERNAME', 'search1_test_bot')

# Create the main app
app = FastAPI(title="УЗРИ - Telegram Bot API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Models
class User(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    attempts_remaining: int = 0
    referred_by: Optional[int] = None
    referral_code: str
    total_referrals: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_admin: bool = False
    last_active: datetime = Field(default_factory=datetime.utcnow)
    is_subscribed: bool = False

class Search(BaseModel):
    user_id: int
    query: str
    search_type: str
    results: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    attempt_used: bool = True
    success: bool = True
    cost: float = 0.0

class Referral(BaseModel):
    referrer_id: int
    referred_id: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    attempt_given: bool = True

# Helper Functions
def generate_referral_code(telegram_id: int) -> str:
    """Generate unique referral code"""
    data = f"{telegram_id}_{secrets.token_hex(8)}"
    return hashlib.md5(data.encode()).hexdigest()[:8]

def detect_search_type(query: str) -> str:
    """Detect search type based on query pattern"""
    query = query.strip()
    
    # Phone number patterns
    phone_patterns = [
        r'^\+?[7-8]\d{10}$',  # Russian numbers
        r'^\+?\d{10,15}$',    # International numbers
        r'^[7-8]\(\d{3}\)\d{3}-?\d{2}-?\d{2}$'  # Formatted Russian
    ]
    
    for pattern in phone_patterns:
        if re.match(pattern, query.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')):
            return "📱 Телефон"
    
    # Email pattern
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', query):
        return "📧 Email"
    
    # Car number pattern (Russian)
    if re.match(r'^[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$', query.upper().replace(' ', '')):
        return "🚗 Автомобиль"
    
    # Username/nickname pattern
    if query.startswith('@') or re.match(r'^[a-zA-Z0-9_]+$', query):
        return "🆔 Никнейм"
    
    # IP address pattern
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', query):
        return "🌐 IP-адрес"
    
    # Address pattern
    address_keywords = ['улица', 'ул', 'проспект', 'пр', 'переулок', 'пер', 'дом', 'д', 'квартира', 'кв']
    if any(keyword in query.lower() for keyword in address_keywords):
        return "🏠 Адрес"
    
    # Name pattern
    words = query.split()
    if 2 <= len(words) <= 3 and all(re.match(r'^[а-яА-ЯёЁa-zA-Z]+$', word) for word in words):
        return "👤 ФИО"
    
    return "🔍 Общий поиск"

def create_main_menu():
    """Create main menu keyboard"""
    return {
        "inline_keyboard": [
            [
                {"text": "🔍 Поиск", "callback_data": "menu_search"},
                {"text": "👤 Профиль", "callback_data": "menu_profile"}
            ],
            [
                {"text": "💡 Проверка (бесплатно)", "callback_data": "menu_check"},
                {"text": "📊 Базы данных", "callback_data": "menu_sources"}
            ],
            [
                {"text": "🔗 Реферальная программа", "callback_data": "menu_referral"},
                {"text": "❓ Помощь", "callback_data": "menu_help"}
            ]
        ]
    }

def create_back_keyboard():
    """Create back button keyboard"""
    return {
        "inline_keyboard": [
            [{"text": "◀️ Назад в меню", "callback_data": "back_to_menu"}]
        ]
    }

def create_subscription_keyboard():
    """Create subscription check keyboard"""
    return {
        "inline_keyboard": [
            [
                {"text": "📢 Подписаться на канал", "url": "https://t.me/uzri_sebya"}
            ],
            [
                {"text": "✅ Проверить подписку", "callback_data": "check_subscription"}
            ]
        ]
    }

async def usersbox_request(endpoint: str, params: Dict = None) -> Dict:
    """Make request to usersbox API"""
    headers = {"Authorization": USERSBOX_TOKEN}
    url = f"{USERSBOX_BASE_URL}{endpoint}"
    
    try:
        response = requests.get(url, headers=headers, params=params or {}, timeout=30)
        return response.json()
    except Exception as e:
        logging.error(f"Usersbox API error: {e}")
        return {"status": "error", "error": {"message": str(e)}}

def format_search_results(results: Dict[str, Any], query: str, search_type: str) -> str:
    """Format usersbox API results for Telegram"""
    if results.get('status') == 'error':
        return f"❌ *Ошибка:* {results.get('error', {}).get('message', 'Неизвестная ошибка')}"

    data = results.get('data', {})
    total_count = data.get('count', 0)
    
    if total_count == 0:
        return f"🔍 *Поиск:* `{query}`\n{search_type}\n\n❌ *Результатов не найдено*\n\n💡 *Попробуйте изменить формат запроса*"
    
    formatted_text = f"🎯 *РЕЗУЛЬТАТЫ ПОИСКА*\n\n"
    formatted_text += f"🔍 *Запрос:* `{query}`\n"
    formatted_text += f"📂 *Тип:* {search_type}\n"
    formatted_text += f"📊 *Найдено:* {total_count} записей\n\n"

    # Format search results
    if 'items' in data and isinstance(data['items'], list):
        formatted_text += "📋 *ДАННЫЕ ИЗ БАЗ:*\n\n"
        
        for i, source_data in enumerate(data['items'][:5], 1):
            if 'source' in source_data and 'hits' in source_data:
                source = source_data['source']
                hits = source_data['hits']
                hits_count = hits.get('hitsCount', hits.get('count', 0))
                
                # Database name translation
                db_names = {
                    'yandex': '🟡 Яндекс',
                    'avito': '🟢 Авито',
                    'vk': '🔵 ВКонтакте',
                    'ok': '🟠 Одноклассники',
                    'delivery_club': '🍕 Delivery Club',
                    'cdek': '📦 СДЭК'
                }
                
                db_display = db_names.get(source.get('database', ''), f"📊 {source.get('database', 'N/A')}")
                
                formatted_text += f"*{i}. {db_display}*\n"
                formatted_text += f"📁 База: {source.get('collection', 'N/A')}\n"
                formatted_text += f"🔢 Записей: {hits_count}\n"

                # Format individual items
                if 'items' in hits and hits['items']:
                    formatted_text += "💾 *Данные:*\n"
                    for item in hits['items'][:2]:
                        for key, value in item.items():
                            if key.startswith('_'):
                                continue
                            
                            if key in ['phone', 'телефон', 'tel', 'mobile']:
                                formatted_text += f"📞 {value}\n"
                            elif key in ['email', 'почта', 'mail', 'e_mail']:
                                formatted_text += f"📧 {value}\n"
                            elif key in ['full_name', 'name', 'имя', 'фио', 'first_name', 'last_name']:
                                formatted_text += f"👤 {value}\n"
                            elif key in ['birth_date', 'birthday', 'дата_рождения', 'bdate']:
                                formatted_text += f"🎂 {value}\n"
                            elif key in ['address', 'адрес', 'city', 'город']:
                                formatted_text += f"🏠 {value}\n"
                            elif key in ['sex', 'gender', 'пол']:
                                gender_map = {'1': 'Ж', '2': 'М', 'male': 'М', 'female': 'Ж'}
                                formatted_text += f"⚥ {gender_map.get(str(value), value)}\n"
                
                formatted_text += "\n"

    formatted_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    formatted_text += "🔒 *Конфиденциальность:* Используйте данные ответственно\n"
    formatted_text += "💰 *Стоимость поиска:* 2.5 ₽"
    
    return formatted_text

def format_explain_results(results: Dict[str, Any], query: str) -> str:
    """Format explain results (free check)"""
    if results.get('status') == 'error':
        return f"❌ *Ошибка:* {results.get('error', {}).get('message', 'Неизвестная ошибка')}"

    data = results.get('data', {})
    total_count = data.get('count', 0)
    
    if total_count == 0:
        return f"🔍 *Проверка:* `{query}`\n\n❌ *Данных не найдено*\n\n💡 *Попробуйте изменить формат*"
    
    formatted_text = f"📊 *БЫСТРАЯ ПРОВЕРКА* (бесплатно)\n\n"
    formatted_text += f"🔍 *Запрос:* `{query}`\n"
    formatted_text += f"📈 *Всего найдено:* {total_count} записей\n\n"

    if 'items' in data and isinstance(data['items'], list):
        formatted_text += "📋 *Распределение по базам:*\n\n"
        for i, item in enumerate(data['items'][:10], 1):
            source = item.get('source', {})
            hits = item.get('hits', {})
            count = hits.get('count', 0)
            
            db_names = {
                'yandex': '🟡 Яндекс',
                'avito': '🟢 Авито', 
                'vk': '🔵 ВК',
                'ok': '🟠 ОК',
                'delivery_club': '🍕 DC',
                'cdek': '📦 СДЭК'
            }
            
            db_display = db_names.get(source.get('database', ''), source.get('database', 'N/A'))
            formatted_text += f"*{i}.* {db_display}: {count} записей\n"

    formatted_text += f"\n💰 *Полный поиск с данными:* 2.5 ₽\n"
    formatted_text += f"🆓 *Эта проверка:* БЕСПЛАТНО"
    
    return formatted_text

async def check_subscription(user_id: int) -> bool:
    """Check if user is subscribed to required channel"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getChatMember"
        params = {
            "chat_id": REQUIRED_CHANNEL,
            "user_id": user_id
        }
        
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                status = data.get('result', {}).get('status')
                return status in ['member', 'administrator', 'creator']
        
        return False
    except Exception as e:
        logging.error(f"Subscription check error: {e}")
        return False

async def send_telegram_message(chat_id: int, text: str, parse_mode: str = "Markdown", reply_markup: dict = None) -> bool:
    """Send message to Telegram user"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logging.error(f"Failed to send Telegram message: {e}")
        return False

async def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None, last_name: str = None) -> User:
    """Get existing user or create new one"""
    user_data = await db.users.find_one({"telegram_id": telegram_id})
    
    if user_data:
        # Update last active and user info
        await db.users.update_one(
            {"telegram_id": telegram_id},
            {
                "$set": {
                    "last_active": datetime.utcnow(),
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name
                }
            }
        )
        return User(**user_data)
    else:
        # Create new user
        referral_code = generate_referral_code(telegram_id)
        is_admin = username == ADMIN_USERNAME if username else False
        
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            referral_code=referral_code,
            is_admin=is_admin,
            attempts_remaining=999 if is_admin else 3  # Admin gets unlimited, new users get 3 free attempts
        )
        
        await db.users.insert_one(user.dict())
        return user

# API Routes
@api_router.get("/")
async def root():
    return {"message": "УЗРИ - Telegram Bot API", "status": "running"}

@api_router.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    """Handle Telegram webhook"""
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    
    try:
        update_data = await request.json()
        await handle_telegram_update(update_data)
        return {"status": "ok"}
    except Exception as e:
        logging.error(f"Webhook processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

async def handle_callback_query(callback_query: Dict[str, Any]):
    """Handle callback queries from inline keyboard buttons"""
    chat_id = callback_query.get('message', {}).get('chat', {}).get('id')
    user_id = callback_query.get('from', {}).get('id')
    data = callback_query.get('data')
    callback_query_id = callback_query.get('id')
    
    # Answer callback query
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
        requests.post(url, json={"callback_query_id": callback_query_id}, timeout=5)
    except:
        pass
    
    # Get user
    user = await get_or_create_user(
        telegram_id=user_id,
        username=callback_query.get('from', {}).get('username'),
        first_name=callback_query.get('from', {}).get('first_name'),
        last_name=callback_query.get('from', {}).get('last_name')
    )
    
    # Handle different callback data
    if data == "check_subscription":
        await handle_subscription_check(chat_id, user_id)
    elif data == "back_to_menu":
        await show_main_menu(chat_id, user)
    elif data == "menu_search":
        await show_search_menu(chat_id, user)
    elif data == "menu_profile":
        await show_profile_menu(chat_id, user)
    elif data == "menu_check":
        await show_check_menu(chat_id, user)
    elif data == "menu_sources":
        await show_sources_menu(chat_id, user)
    elif data == "menu_referral":
        await show_referral_menu(chat_id, user)
    elif data == "menu_help":
        await show_help_menu(chat_id, user)

async def handle_subscription_check(chat_id: int, user_id: int):
    """Handle subscription check"""
    is_subscribed = await check_subscription(user_id)
    if is_subscribed:
        await db.users.update_one(
            {"telegram_id": user_id},
            {"$set": {"is_subscribed": True}}
        )
        
        await send_telegram_message(
            chat_id,
            "✅ *Подписка подтверждена!*\n\n🎉 Теперь вы можете пользоваться всеми функциями сервиса!"
        )
    else:
        await send_telegram_message(
            chat_id,
            "❌ *Подписка не найдена*\n\n📢 Подпишитесь на канал @uzri_sebya и попробуйте снова",
            reply_markup=create_subscription_keyboard()
        )

async def show_main_menu(chat_id: int, user: User):
    """Show main menu"""
    welcome_text = f"🎯 *СЕРВИС - УЗРИ*\n\n"
    welcome_text += f"👋 Добро пожаловать, {user.first_name or 'пользователь'}!\n\n"
    welcome_text += f"💎 *Попыток:* {user.attempts_remaining}\n"
    welcome_text += f"👥 *Рефералов:* {user.total_referrals}\n\n"
    welcome_text += f"🔍 *Выберите действие:*"
    
    await send_telegram_message(chat_id, welcome_text, reply_markup=create_main_menu())

async def show_search_menu(chat_id: int, user: User):
    """Show search menu"""
    if not user.is_admin:
        is_subscribed = await check_subscription(user.telegram_id)
        if not is_subscribed:
            await send_telegram_message(
                chat_id,
                "🔒 *Для поиска нужна подписка!*\n\n📢 Подпишитесь на @uzri_sebya",
                reply_markup=create_subscription_keyboard()
            )
            return
    
    search_text = f"🔍 *ПОИСК ПО БАЗАМ ДАННЫХ*\n\n"
    search_text += f"💰 *Стоимость:* 2.5 ₽ за запрос\n"
    search_text += f"💎 *Ваши попытки:* {user.attempts_remaining}\n\n"
    search_text += f"📝 *Что можно искать:*\n"
    search_text += f"📱 Телефон: +79123456789\n"
    search_text += f"📧 Email: user@mail.ru\n"
    search_text += f"👤 ФИО: Иван Петров\n"
    search_text += f"🚗 Авто: А123ВС777\n"
    search_text += f"🆔 Никнейм: @username\n\n"
    search_text += f"➡️ *Просто отправьте данные для поиска*"
    
    await send_telegram_message(chat_id, search_text, reply_markup=create_back_keyboard())

async def show_profile_menu(chat_id: int, user: User):
    """Show profile menu"""
    # Get statistics
    total_searches = await db.searches.count_documents({"user_id": user.telegram_id})
    successful_searches = await db.searches.count_documents({"user_id": user.telegram_id, "success": True})
    
    profile_text = f"👤 *ВАШ ПРОФИЛЬ*\n\n"
    profile_text += f"🆔 *ID:* `{user.telegram_id}`\n"
    profile_text += f"👤 *Имя:* {user.first_name or 'N/A'}\n"
    profile_text += f"🔗 *Username:* @{user.username or 'N/A'}\n\n"
    profile_text += f"📊 *СТАТИСТИКА:*\n"
    profile_text += f"💎 Попыток: {user.attempts_remaining}\n"
    profile_text += f"🔍 Поисков: {total_searches}\n"
    profile_text += f"✅ Успешных: {successful_searches}\n"
    profile_text += f"👥 Рефералов: {user.total_referrals}\n"
    profile_text += f"📅 Регистрация: {user.created_at.strftime('%d.%m.%Y')}\n\n"
    
    if user.is_admin:
        profile_text += f"👑 *Статус:* АДМИНИСТРАТОР\n"
    
    await send_telegram_message(chat_id, profile_text, reply_markup=create_back_keyboard())

async def show_check_menu(chat_id: int, user: User):
    """Show free check menu"""
    check_text = f"💡 *БЕСПЛАТНАЯ ПРОВЕРКА*\n\n"
    check_text += f"🆓 *Стоимость:* БЕСПЛАТНО\n"
    check_text += f"⚡ *Лимит:* 300 запросов в минуту\n\n"
    check_text += f"📊 *Что показывает:*\n"
    check_text += f"• Количество найденных записей\n"
    check_text += f"• Распределение по базам данных\n"
    check_text += f"• БЕЗ показа самих данных\n\n"
    check_text += f"💡 *Используйте для:*\n"
    check_text += f"• Проверки наличия данных\n"
    check_text += f"• Оценки количества утечек\n"
    check_text += f"• Экономии средств\n\n"
    check_text += f"➡️ *Отправьте данные для проверки*"
    
    await send_telegram_message(chat_id, check_text, reply_markup=create_back_keyboard())

async def show_sources_menu(chat_id: int, user: User):
    """Show sources menu"""
    try:
        sources_result = await usersbox_request("/sources")
        
        if sources_result.get('status') == 'success':
            data = sources_result.get('data', {})
            total_sources = data.get('count', 0)
            sources = data.get('items', [])[:10]  # Show top 10
            
            sources_text = f"📊 *ДОСТУПНЫЕ БАЗЫ ДАННЫХ*\n\n"
            sources_text += f"🗄️ *Всего баз:* {total_sources}\n"
            sources_text += f"📈 *Записей:* ~20 миллиардов\n\n"
            sources_text += f"🔝 *ТОП-10 БАЗ:*\n\n"
            
            for i, source in enumerate(sources, 1):
                title = source.get('title', 'N/A')[:30]
                count = source.get('count', 0)
                sources_text += f"*{i}.* {title}\n"
                sources_text += f"📊 {count:,} записей\n\n"
        else:
            sources_text = "❌ Ошибка загрузки списка баз данных"
    
    except Exception as e:
        sources_text = "❌ Ошибка при получении списка баз"
    
    await send_telegram_message(chat_id, sources_text, reply_markup=create_back_keyboard())

async def show_referral_menu(chat_id: int, user: User):
    """Show referral menu"""
    referral_link = f"https://t.me/{BOT_USERNAME}?start={user.referral_code}"
    
    referral_text = f"🔗 *РЕФЕРАЛЬНАЯ ПРОГРАММА*\n\n"
    referral_text += f"🎁 *За каждого друга:* +3 попытки\n"
    referral_text += f"💝 *Друг получает:* +3 попытки\n\n"
    referral_text += f"📊 *ВАША СТАТИСТИКА:*\n"
    referral_text += f"👥 Приглашено: {user.total_referrals}\n"
    referral_text += f"💎 Заработано: {user.total_referrals * 3} попыток\n\n"
    referral_text += f"🔗 *ВАША ССЫЛКА:*\n"
    referral_text += f"`{referral_link}`\n\n"
    referral_text += f"📱 *Поделитесь ссылкой в:*\n"
    referral_text += f"• WhatsApp, Viber\n"
    referral_text += f"• ВКонтакте, Instagram\n"
    referral_text += f"• С друзьями и семьей"
    
    await send_telegram_message(chat_id, referral_text, reply_markup=create_back_keyboard())

async def show_help_menu(chat_id: int, user: User):
    """Show help menu"""
    help_text = f"❓ *СПРАВКА И ПОМОЩЬ*\n\n"
    help_text += f"🎯 *О СЕРВИСЕ:*\n"
    help_text += f"УЗРИ помогает найти информацию о себе или близких из открытых источников интернета.\n\n"
    help_text += f"💰 *ТАРИФЫ:*\n"
    help_text += f"🔍 Полный поиск: 2.5 ₽\n"
    help_text += f"💡 Проверка: БЕСПЛАТНО\n\n"
    help_text += f"🎁 *БЕСПЛАТНЫЕ ПОПЫТКИ:*\n"
    help_text += f"• При регистрации: 3 попытки\n"
    help_text += f"• За реферала: +3 попытки\n\n"
    help_text += f"📞 *ПОДДЕРЖКА:*\n"
    help_text += f"@eriksson_sop - администратор\n\n"
    help_text += f"⚖️ *ВАЖНО:*\n"
    help_text += f"• Используйте данные ответственно\n"
    help_text += f"• Соблюдайте законы РФ\n"
    help_text += f"• Не нарушайте приватность"
    
    await send_telegram_message(chat_id, help_text, reply_markup=create_back_keyboard())

async def handle_telegram_update(update_data: Dict[str, Any]):
    """Process incoming Telegram update"""
    # Handle callback queries
    callback_query = update_data.get('callback_query')
    if callback_query:
        await handle_callback_query(callback_query)
        return
    
    message = update_data.get('message')
    if not message:
        return

    chat_id = message.get('chat', {}).get('id')
    text = message.get('text', '')
    user_info = message.get('from', {})
    
    if not chat_id:
        return

    # Get or create user
    user = await get_or_create_user(
        telegram_id=user_info.get('id', chat_id),
        username=user_info.get('username'),
        first_name=user_info.get('first_name'),
        last_name=user_info.get('last_name')
    )

    # Handle /start command
    if text.startswith('/start'):
        # Check for referral
        parts = text.split()
        if len(parts) > 1:
            referral_code = parts[1]
            await process_referral(user.telegram_id, referral_code)
        
        # Check subscription for non-admin
        if not user.is_admin:
            is_subscribed = await check_subscription(user.telegram_id)
            if not is_subscribed:
                await send_telegram_message(
                    chat_id,
                    f"🎯 *ДОБРО ПОЖАЛОВАТЬ В УЗРИ!*\n\n🔒 *Для использования сервиса подпишитесь на канал @uzri_sebya*",
                    reply_markup=create_subscription_keyboard()
                )
                return
        
        await show_main_menu(chat_id, user)
    
    # Handle admin commands
    elif text.startswith('/admin') and user.is_admin:
        await handle_admin_commands(chat_id, user)
    
    # Handle search queries
    else:
        await handle_search_query(chat_id, text, user)

async def handle_search_query(chat_id: int, query: str, user: User):
    """Handle search query"""
    # Check subscription for non-admin
    if not user.is_admin:
        is_subscribed = await check_subscription(user.telegram_id)
        if not is_subscribed:
            await send_telegram_message(
                chat_id,
                "🔒 Для поиска нужна подписка на @uzri_sebya",
                reply_markup=create_subscription_keyboard()
            )
            return
    
    # Check if this is a free check (starts with specific keywords)
    if query.lower().startswith(('проверь', 'check', 'сколько', 'количество')):
        actual_query = query.split(' ', 1)[1] if ' ' in query else query
        await handle_free_check(chat_id, actual_query, user)
        return
    
    # Full search
    if user.attempts_remaining <= 0 and not user.is_admin:
        await send_telegram_message(
            chat_id,
            "❌ *Попытки закончились!*\n\n🔗 Пригласите друзей для получения новых попыток",
            reply_markup=create_main_menu()
        )
        return
    
    # Detect search type and perform search
    search_type = detect_search_type(query)
    
    await send_telegram_message(
        chat_id,
        f"🔍 *Выполняю поиск...*\n{search_type}\n⏱️ Подождите..."
    )
    
    try:
        # Perform search
        results = await usersbox_request("/search", {"q": query})
        
        # Format and send results
        formatted_results = format_search_results(results, query, search_type)
        await send_telegram_message(chat_id, formatted_results, reply_markup=create_main_menu())
        
        # Save search record
        search = Search(
            user_id=user.telegram_id,
            query=query,
            search_type=search_type,
            results=results,
            success=results.get('status') == 'success',
            cost=2.5
        )
        await db.searches.insert_one(search.dict())
        
        # Deduct attempt (except for admin)
        if not user.is_admin and results.get('status') == 'success':
            await db.users.update_one(
                {"telegram_id": user.telegram_id},
                {"$inc": {"attempts_remaining": -1}}
            )
    
    except Exception as e:
        await send_telegram_message(
            chat_id,
            "❌ Ошибка при выполнении поиска. Попробуйте позже.",
            reply_markup=create_main_menu()
        )

async def handle_free_check(chat_id: int, query: str, user: User):
    """Handle free check query"""
    await send_telegram_message(
        chat_id,
        f"💡 *Бесплатная проверка...*\n🔍 {query}"
    )
    
    try:
        # Use explain endpoint (free)
        results = await usersbox_request("/explain", {"q": query})
        
        # Format and send results
        formatted_results = format_explain_results(results, query)
        await send_telegram_message(chat_id, formatted_results, reply_markup=create_main_menu())
    
    except Exception as e:
        await send_telegram_message(
            chat_id,
            "❌ Ошибка при проверке. Попробуйте позже.",
            reply_markup=create_main_menu()
        )

async def process_referral(referred_user_id: int, referral_code: str) -> bool:
    """Process referral and give attempts"""
    try:
        # Find referrer by code
        referrer = await db.users.find_one({"referral_code": referral_code})
        if not referrer or referrer['telegram_id'] == referred_user_id:
            return False

        # Check if referral already exists
        existing_referral = await db.referrals.find_one({
            "referrer_id": referrer['telegram_id'],
            "referred_id": referred_user_id
        })
        if existing_referral:
            return False

        # Create referral record
        referral = Referral(
            referrer_id=referrer['telegram_id'],
            referred_id=referred_user_id
        )
        await db.referrals.insert_one(referral.dict())

        # Give 3 attempts to referrer and update count
        await db.users.update_one(
            {"telegram_id": referrer['telegram_id']},
            {
                "$inc": {
                    "attempts_remaining": 3,
                    "total_referrals": 1
                }
            }
        )

        # Give 3 attempts to referred user
        await db.users.update_one(
            {"telegram_id": referred_user_id},
            {
                "$set": {"referred_by": referrer['telegram_id']},
                "$inc": {"attempts_remaining": 3}
            }
        )

        # Notify referrer
        await send_telegram_message(
            referrer['telegram_id'],
            f"🎉 *Новый реферал!*\n\n💎 Вы получили +3 попытки\n👥 Всего рефералов: {referrer['total_referrals'] + 1}"
        )

        return True
    except Exception as e:
        logging.error(f"Referral processing error: {e}")
        return False

async def handle_admin_commands(chat_id: int, user: User):
    """Handle admin commands"""
    # Get system statistics
    total_users = await db.users.count_documents({})
    total_searches = await db.searches.count_documents({})
    total_referrals = await db.referrals.count_documents({})
    
    admin_text = f"👑 *АДМИН ПАНЕЛЬ*\n\n"
    admin_text += f"📊 *СТАТИСТИКА:*\n"
    admin_text += f"👥 Пользователей: {total_users}\n"
    admin_text += f"🔍 Поисков: {total_searches}\n"
    admin_text += f"🔗 Рефералов: {total_referrals}\n\n"
    admin_text += f"🛠️ *Управление через веб-интерфейс*"
    
    await send_telegram_message(chat_id, admin_text, reply_markup=create_main_menu())

# API endpoints for web dashboard
@api_router.get("/users")
async def get_users():
    """Get all users"""
    users = await db.users.find().to_list(1000)
    for user in users:
        user["_id"] = str(user["_id"])
    return users

@api_router.get("/stats")
async def get_stats():
    """Get bot statistics"""
    total_users = await db.users.count_documents({})
    total_searches = await db.searches.count_documents({})
    total_referrals = await db.referrals.count_documents({})
    successful_searches = await db.searches.count_documents({"success": True})

    return {
        "total_users": total_users,
        "total_searches": total_searches,
        "total_referrals": total_referrals,
        "successful_searches": successful_searches,
        "success_rate": (successful_searches / total_searches * 100) if total_searches > 0 else 0
    }

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()