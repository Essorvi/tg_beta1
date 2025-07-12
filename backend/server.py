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
app = FastAPI(title="Usersbox Telegram Bot API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Models
class User(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    attempts_remaining: int = 0  # Changed to 0 by default
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
    search_type: str  # phone, email, name, etc.
    results: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    attempt_used: bool = True
    success: bool = True

class Referral(BaseModel):
    referrer_id: int
    referred_id: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    attempt_given: bool = True

class TelegramMessage(BaseModel):
    chat_id: int
    text: str
    parse_mode: str = "Markdown"

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
            return "phone"
    
    # Email pattern
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', query):
        return "email"
    
    # Car number pattern (Russian)
    if re.match(r'^[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$', query.upper().replace(' ', '')):
        return "car_number"
    
    # Username/nickname pattern
    if query.startswith('@') or re.match(r'^[a-zA-Z0-9_]+$', query):
        return "username"
    
    # IP address pattern
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', query):
        return "ip_address"
    
    # Address pattern (contains typical address words)
    address_keywords = ['улица', 'ул', 'проспект', 'пр', 'переулок', 'пер', 'дом', 'д', 'квартира', 'кв']
    if any(keyword in query.lower() for keyword in address_keywords):
        return "address"
    
    # Name pattern (2-3 words, Cyrillic or Latin)
    words = query.split()
    if 2 <= len(words) <= 3 and all(re.match(r'^[а-яА-ЯёЁa-zA-Z]+$', word) for word in words):
        return "name"
    
    # Default to general search
    return "general"

def format_search_results(results: Dict[str, Any], query: str, search_type: str) -> str:
    """Format usersbox API results for Telegram with enhanced display"""
    if results.get('status') == 'error':
        return f"❌ *Ошибка поиска:* {results.get('error', {}).get('message', 'Неизвестная ошибка')}"

    data = results.get('data', {})
    total_count = data.get('count', 0)
    
    if total_count == 0:
        return f"🔍 *Поиск по запросу:* `{query}`\n\n❌ *Результатов не найдено*\n\n💡 *Попробуйте:*\n• Другой формат номера\n• Полное имя и фамилию\n• Проверить правописание"

    # Create search type emoji mapping
    type_emojis = {
        "phone": "📱",
        "email": "📧", 
        "name": "👤",
        "car_number": "🚗",
        "username": "🆔",
        "ip_address": "🌐",
        "address": "🏠",
        "general": "🔍"
    }
    
    search_emoji = type_emojis.get(search_type, "🔍")
    
    formatted_text = f"{search_emoji} *Поиск по запросу:* `{query}`\n"
    formatted_text += f"🔎 *Тип поиска:* {search_type}\n\n"
    formatted_text += f"📊 *Всего найдено:* {total_count} записей\n\n"

    # Format search results from /search endpoint
    if 'items' in data and isinstance(data['items'], list):
        formatted_text += "📋 *Результаты поиска:*\n\n"
        
        for i, source_data in enumerate(data['items'][:5], 1):  # Limit to 5 sources
            if 'source' in source_data and 'hits' in source_data:
                source = source_data['source']
                hits = source_data['hits']
                hits_count = hits.get('hitsCount', hits.get('count', 0))
                
                # Database name translation
                db_names = {
                    'yandex': 'Яндекс',
                    'avito': 'Авито',
                    'vk': 'ВКонтакте',
                    'ok': 'Одноклассники',
                    'delivery_club': 'Delivery Club',
                    'cdek': 'СДЭК'
                }
                
                db_display = db_names.get(source.get('database', ''), source.get('database', 'N/A'))
                
                formatted_text += f"*{i}. База данных:* {db_display}\n"
                formatted_text += f" *Коллекция:* {source.get('collection', 'N/A')}\n"
                formatted_text += f" *Найдено записей:* {hits_count}\n"

                # Format individual items if available
                if 'items' in hits and hits['items']:
                    formatted_text += " *Данные:*\n"
                    for item in hits['items'][:2]:  # Show first 2 items per source
                        for key, value in item.items():
                            if key.startswith('_'):
                                continue  # Skip internal fields
                            
                            if key in ['phone', 'телефон', 'tel', 'mobile']:
                                formatted_text += f" 📞 Телефон: `{value}`\n"
                            elif key in ['email', 'почта', 'mail', 'e_mail']:
                                formatted_text += f" 📧 Email: `{value}`\n"
                            elif key in ['full_name', 'name', 'имя', 'фио', 'first_name', 'last_name']:
                                formatted_text += f" 👤 Имя: `{value}`\n"
                            elif key in ['birth_date', 'birthday', 'дата_рождения', 'bdate']:
                                formatted_text += f" 🎂 Дата рождения: `{value}`\n"
                            elif key in ['address', 'адрес', 'city', 'город']:
                                if isinstance(value, dict):
                                    addr_parts = []
                                    for addr_key, addr_val in value.items():
                                        if addr_val:
                                            addr_parts.append(f"{addr_val}")
                                    if addr_parts:
                                        formatted_text += f" 🏠 Адрес: `{', '.join(addr_parts)}`\n"
                                else:
                                    formatted_text += f" 🏠 Адрес: `{value}`\n"
                            elif key in ['sex', 'gender', 'пол']:
                                gender_map = {'1': 'Женский', '2': 'Мужской', 'male': 'Мужской', 'female': 'Женский'}
                                formatted_text += f" ⚥ Пол: `{gender_map.get(str(value), value)}`\n"
                            elif key in ['age', 'возраст']:
                                formatted_text += f" 🎂 Возраст: `{value}`\n"
                            elif key in ['vk_id', 'user_id', 'id']:
                                formatted_text += f" 🆔 ID: `{value}`\n"
                            else:
                                # Generic field formatting
                                if isinstance(value, (str, int, float)) and len(str(value)) < 100:
                                    formatted_text += f" • {key}: `{value}`\n"
                        
                        formatted_text += "\n"

    # Format explain results
    elif 'count' in data and isinstance(data.get('items'), list):
        formatted_text += "📋 *Распределение по базам:*\n\n"
        for i, item in enumerate(data['items'][:10], 1):  # Show top 10
            source = item.get('source', {})
            hits = item.get('hits', {})
            count = hits.get('count', 0)
            
            db_display = source.get('database', 'N/A')
            if db_display in ['yandex', 'avito', 'vk', 'ok']:
                db_display = db_display.upper()
            
            formatted_text += f"*{i}.* {db_display} / {source.get('collection', 'N/A')}: {count} записей\n"

    # Add security and usage note
    formatted_text += "\n🔒 *Безопасность:*\n"
    formatted_text += "• Используйте данные ответственно\n"
    formatted_text += "• Соблюдайте приватность\n"
    formatted_text += "• Не нарушайте законы\n\n"
    formatted_text += "💡 *Примечание:* Показаны основные результаты из открытых источников."
    
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

async def send_telegram_message(chat_id: int, text: str, parse_mode: str = None, reply_markup: dict = None) -> bool:
    """Send message to Telegram user with optional keyboard"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    
    # Only add parse_mode if it's explicitly set
    if parse_mode:
        payload["parse_mode"] = parse_mode
    
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    try:
        logging.info(f"Sending message to chat_id={chat_id}, text length={len(text)}")
        response = requests.post(url, json=payload, timeout=10)
        logging.info(f"Telegram API response: status={response.status_code}, response={response.text}")
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
            attempts_remaining=999 if is_admin else 0  # Admin gets unlimited, others get 0
        )
        
        await db.users.insert_one(user.dict())
        return user

async def process_referral(referred_user_id: int, referral_code: str) -> bool:
    """Process referral and give attempt to referrer"""
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

        # Give attempt to referrer and update referral count
        await db.users.update_one(
            {"telegram_id": referrer['telegram_id']},
            {
                "$inc": {
                    "attempts_remaining": 1,
                    "total_referrals": 1
                }
            }
        )

        # Give 1 attempt to referred user
        await db.users.update_one(
            {"telegram_id": referred_user_id},
            {
                "$set": {"referred_by": referrer['telegram_id']},
                "$inc": {"attempts_remaining": 1}
            }
        )

        # Notify referrer
        await send_telegram_message(
            referrer['telegram_id'],
            f"🎉 *Поздравляем!* Пользователь присоединился по вашей реферальной ссылке!\n\n"
            f"💎 Вы получили +1 попытку поиска\n"
            f"👥 Всего рефералов: {referrer['total_referrals'] + 1}"
        )

        return True
    except Exception as e:
        logging.error(f"Referral processing error: {e}")
        return False

# API Routes
@api_router.get("/")
async def root():
    return {"message": "Usersbox Telegram Bot API", "status": "running"}

@api_router.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    """Handle Telegram webhook"""
    logging.info(f"Webhook called with secret: {secret}")
    
    if secret != WEBHOOK_SECRET:
        logging.error(f"Invalid webhook secret received: {secret}, expected: {WEBHOOK_SECRET}")
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    
    try:
        raw_body = await request.body()
        logging.info(f"Raw webhook body: {raw_body}")
        
        update_data = await request.json()
        logging.info(f"Parsed webhook data: {update_data}")
        
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
    
    if not chat_id or not user_id or not data:
        logging.error("Missing required callback data")
        return
    
    # Answer callback query to remove loading
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
        requests.post(url, json={"callback_query_id": callback_query_id}, timeout=5)
    except Exception as e:
        logging.error(f"Failed to answer callback query: {e}")
    
    if data == "check_subscription":
        # Get user
        user = await get_or_create_user(
            telegram_id=user_id,
            username=callback_query.get('from', {}).get('username'),
            first_name=callback_query.get('from', {}).get('first_name'),
            last_name=callback_query.get('from', {}).get('last_name')
        )
        
        # Check subscription
        is_subscribed = await check_subscription(user_id)
        if is_subscribed:
            # Update user subscription status
            await db.users.update_one(
                {"telegram_id": user_id},
                {"$set": {"is_subscribed": True}}
            )
            
            await send_telegram_message(
                chat_id,
                "✅ Подписка подтверждена!\n\n"
                "🎉 Теперь вы можете пользоваться всеми функциями бота!\n"
                "💡 Отправьте любой запрос для поиска или используйте команду /help"
            )
        else:
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "📢 Подписаться на канал", "url": "https://t.me/uzri_sebya"}
                    ],
                    [
                        {"text": "✅ Проверить подписку", "callback_data": "check_subscription"}
                    ]
                ]
            }
            
            await send_telegram_message(
                chat_id,
                "❌ Подписка не найдена\n\n"
                "📢 Подпишитесь на канал @uzri_sebya и нажмите 'Проверить подписку' снова",
                reply_markup=keyboard
            )

async def handle_telegram_update(update_data: Dict[str, Any]):
    """Process incoming Telegram update"""
    logging.info(f"Received telegram update: {update_data}")
    
    # Handle callback queries (button presses)
    callback_query = update_data.get('callback_query')
    if callback_query:
        await handle_callback_query(callback_query)
        return
    
    message = update_data.get('message')
    if not message:
        logging.info("No message in update")
        return

    chat = message.get('chat', {})
    chat_id = chat.get('id')
    text = message.get('text', '')
    user_info = message.get('from', {})
    
    logging.info(f"Processing message: chat_id={chat_id}, text='{text}', user={user_info.get('username', 'unknown')}")
    
    if not chat_id:
        logging.error("No chat_id in message")
        return

    # Get or create user
    user = await get_or_create_user(
        telegram_id=user_info.get('id', chat_id),
        username=user_info.get('username'),
        first_name=user_info.get('first_name'),
        last_name=user_info.get('last_name')
    )

    # Handle commands
    if text.startswith('/start'):
        await handle_start_command(chat_id, text, user)
    elif text.startswith('/search'):
        await handle_search_command(chat_id, text, user)
    elif text.startswith('/balance'):
        await handle_balance_command(chat_id, user)
    elif text.startswith('/referral'):
        await handle_referral_command(chat_id, user)
    elif text.startswith('/help'):
        await handle_help_command(chat_id, user)
    elif text.startswith('/capabilities'):
        await handle_capabilities_command(chat_id, user)
    elif text.startswith('/admin') and user.is_admin:
        await handle_admin_command(chat_id, text, user)
    elif text.startswith('/give') and user.is_admin:
        await handle_give_attempts_command(chat_id, text, user)
    elif text.startswith('/stats') and user.is_admin:
        await handle_stats_command(chat_id, user)
    else:
        # Check subscription first
        if not user.is_admin:
            is_subscribed = await check_subscription(user.telegram_id)
            if not is_subscribed:
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "📢 Подписаться на канал", "url": "https://t.me/uzri_sebya"}
                        ],
                        [
                            {"text": "✅ Проверить подписку", "callback_data": "check_subscription"}
                        ]
                    ]
                }
                
                await send_telegram_message(
                    chat_id,
                    "🔒 *Для использования бота необходимо подписаться на канал!*\n\n"
                    "📢 Подпишитесь на канал @uzri_sebya и нажмите 'Проверить подписку'\n\n"
                    "💡 После подписки вы сможете пользоваться всеми функциями бота!",
                    reply_markup=keyboard
                )
                return
        
        # Treat as search query if user has attempts
        if user.attempts_remaining > 0 or user.is_admin:
            await handle_search_command(chat_id, f"/search {text}", user)
        else:
            await send_telegram_message(
                chat_id,
                "❌ У вас закончились попытки поиска!\n\n"
                "🔗 Пригласите друзей по реферальной ссылке, чтобы получить больше попыток.\n"
                "Используйте /referral для получения ссылки."
            )

async def handle_start_command(chat_id: int, text: str, user: User):
    """Handle /start command with enhanced welcome"""
    # Check for referral code
    parts = text.split()
    referral_bonus = False
    if len(parts) > 1:
        referral_code = parts[1]
        referral_bonus = await process_referral(user.telegram_id, referral_code)
    
    # Check subscription for non-admin users
    if not user.is_admin:
        is_subscribed = await check_subscription(user.telegram_id)
        if not is_subscribed:
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "📢 Подписаться на канал", "url": "https://t.me/uzri_sebya"}
                    ],
                    [
                        {"text": "✅ Проверить подписку", "callback_data": "check_subscription"}
                    ]
                ]
            }
            
            welcome_text = "🔍 ДОБРО ПОЖАЛОВАТЬ В USERSBOX BOT! 🔍\n\n"
            welcome_text += "🎯 ЧТО УМЕЕТ ЭТОТ БОТ?\n"
            welcome_text += "Этот бот поможет вам найти информацию о себе или близких из открытых источников в интернете. Узнайте, какие данные о вас попали в различные утечки и базы данных.\n\n"
            welcome_text += "🔒 ВАЖНОЕ ТРЕБОВАНИЕ:\n"
            welcome_text += "Для использования бота необходимо подписаться на наш канал!\n\n"
            welcome_text += "📢 Подпишитесь на @uzri_sebya и нажмите 'Проверить подписку'"
            
            await send_telegram_message(chat_id, welcome_text, reply_markup=keyboard)
            return

    # Create simple welcome message without complex formatting
    welcome_text = f"👋 Добро пожаловать, {user.first_name or 'пользователь'}!\n\n"
    
    welcome_text += "🔍 USERSBOX SEARCH BOT\n\n"
    
    welcome_text += "🎯 ЧТО ЭТОТ БОТ УМЕЕТ?\n"
    welcome_text += "Данный бот позволяет \"пробить\" себя или близкого человека, чтобы узнать какая информация о нем слита в открытых источниках интернета.\n\n"
    
    welcome_text += "🔍 ВОЗМОЖНОСТИ ПОИСКА:\n"
    welcome_text += "📱 По номеру телефона (+79123456789)\n"
    welcome_text += "📧 По email адресу (ivan@mail.ru)\n"
    welcome_text += "👤 По ФИО (Иван Петров)\n"
    welcome_text += "🚗 По номеру автомобиля (А123ВС777)\n"
    welcome_text += "🆔 По никнейму (@username)\n"
    welcome_text += "🏠 По адресу (Москва Тверская 1)\n"
    welcome_text += "🌐 По IP адресу (192.168.1.1)\n\n"
    
    welcome_text += "💡 КАК ПОЛЬЗОВАТЬСЯ?\n"
    welcome_text += "Просто отправьте мне:\n"
    welcome_text += "• Номер телефона = поиск по телефону\n"
    welcome_text += "• Email = поиск по почте\n"
    welcome_text += "• Имя Фамилия = поиск по ФИО\n"
    welcome_text += "И так далее!\n\n"

    welcome_text += f"📈 ВАШ СТАТУС:\n"
    welcome_text += f"💎 Попыток поиска: {user.attempts_remaining}\n"
    welcome_text += f"👥 Приглашено друзей: {user.total_referrals}\n"
    welcome_text += f"📅 Дата регистрации: {user.created_at.strftime('%d.%m.%Y')}\n\n"
    
    if referral_bonus:
        welcome_text += "🎉 БОНУС! Вы получили +1 попытку за переход по реферальной ссылке!\n\n"

    welcome_text += "🎮 КОМАНДЫ БОТА:\n"
    welcome_text += "/search [запрос] - поиск информации\n"
    welcome_text += "/balance - проверить баланс попыток\n"
    welcome_text += "/referral - получить реферальную ссылку\n"
    welcome_text += "/help - подробная справка\n"
    welcome_text += "/capabilities - список всех возможностей\n\n"

    if user.is_admin:
        welcome_text += "🔧 АДМИН ПАНЕЛЬ:\n"
        welcome_text += "/admin - панель администратора\n"
        welcome_text += "/give [ID] [попытки] - выдать попытки\n"
        welcome_text += "/stats - полная статистика\n\n"

    welcome_text += "💸 ПОЛУЧИТЬ ПОПЫТКИ:\n"
    welcome_text += "🎁 За каждого приглашенного друга: +1 попытка\n"
    welcome_text += "🔗 Используйте команду /referral для получения ссылки\n\n"

    welcome_text += "🚀 Готов к поиску? Отправьте запрос прямо сейчас!"

    await send_telegram_message(chat_id, welcome_text)

async def handle_capabilities_command(chat_id: int, user: User):
    """Handle capabilities command - detailed list of search capabilities"""
    cap_text = "🎯 *═══════════════════════════*\n"
    cap_text += " 🔍 *ВОЗМОЖНОСТИ ПОИСКА*\n"
    cap_text += "*═══════════════════════════* 🎯\n\n"
    
    cap_text += "📱 *═══ ПОИСК ПО ТЕЛЕФОНУ ═══*\n"
    cap_text += "• Российские номера: `+79123456789`\n"
    cap_text += "• Без плюса: `79123456789`\n"
    cap_text += "• С кодом 8: `89123456789`\n"
    cap_text += "• Форматированные: `+7(912)345-67-89`\n\n"
    
    cap_text += "📧 *═══ ПОИСК ПО EMAIL ═══*\n"
    cap_text += "• Любые домены: `user@mail.ru`\n"
    cap_text += "• Gmail: `user@gmail.com`\n"
    cap_text += "• Яндекс: `user@yandex.ru`\n"
    cap_text += "• Корпоративные: `user@company.com`\n\n"
    
    cap_text += "👤 *═══ ПОИСК ПО ФИО ═══*\n"
    cap_text += "• Полное ФИО: `Иван Петров Сидоров`\n"
    cap_text += "• Имя Фамилия: `Иван Петров`\n"
    cap_text += "• Только имя: `Иван`\n"
    cap_text += "• На латинице: `Ivan Petrov`\n\n"
    
    cap_text += "🚗 *═══ ПОИСК ПО АВТО ═══*\n"
    cap_text += "• Российские номера: `А123ВС777`\n"
    cap_text += "• С пробелами: `А 123 ВС 77`\n"
    cap_text += "• Старый формат: `А123ВС99`\n\n"
    
    cap_text += "🆔 *═══ ПОИСК ПО НИКНЕЙМУ ═══*\n"
    cap_text += "• С собачкой: `@username`\n"
    cap_text += "• Без собачки: `username`\n"
    cap_text += "• ID пользователя: `123456789`\n\n"
    
    cap_text += "🏠 *═══ ПОИСК ПО АДРЕСУ ═══*\n"
    cap_text += "• Полный адрес: `Москва ул Тверская д1`\n"
    cap_text += "• Название улицы: `Тверская улица`\n"
    cap_text += "• Город: `Москва`\n\n"
    
    cap_text += "🌐 *═══ ДОПОЛНИТЕЛЬНО ═══*\n"
    cap_text += "• IP адреса: `192.168.1.1`\n"
    cap_text += "• Общий поиск: любой текст\n\n"
    
    cap_text += "🗃️ *═══ ИСТОЧНИКИ ДАННЫХ ═══*\n"
    cap_text += "• 📱 Мессенджеры (Telegram, WhatsApp)\n"
    cap_text += "• 🌐 Соцсети (VK, OK, Instagram)\n"
    cap_text += "• 🛒 Интернет-магазины (Avito, OZON)\n"
    cap_text += "• 🚚 Доставка (CDEK, Delivery Club)\n"
    cap_text += "• 🏦 Банковские данные\n"
    cap_text += "• 📋 Государственные базы\n"
    cap_text += "• 🎯 И еще 100+ источников!\n\n"
    
    cap_text += "💡 *═══ СОВЕТЫ ПО ПОИСКУ ═══*\n"
    cap_text += "✅ Используйте полные данные\n"
    cap_text += "✅ Проверяйте разные форматы\n"
    cap_text += "✅ Пробуйте все варианты имени\n"
    cap_text += "❌ Не используйте сокращения\n"
    cap_text += "❌ Избегайте опечаток\n\n"
    
    cap_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    cap_text += "🔍 *Просто отправьте данные - бот определит тип автоматически!*"
    
    await send_telegram_message(chat_id, cap_text)

async def handle_search_command(chat_id: int, text: str, user: User):
    """Handle search command with enhanced search type detection"""
    # Extract query
    query = text.replace('/search', '', 1).strip()
    if not query:
        await send_telegram_message(
            chat_id,
            "❌ Ошибка: Укажите запрос для поиска\n\n"
            "Примеры:\n"
            "📱 +79123456789 - поиск по телефону\n"
            "📧 ivan@mail.ru - поиск по email\n"
            "👤 Иван Петров - поиск по имени\n\n"
            "💡 Или используйте /capabilities для полного списка"
        )
        return

    # Check subscription for non-admin users
    if not user.is_admin:
        is_subscribed = await check_subscription(user.telegram_id)
        if not is_subscribed:
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "📢 Подписаться на канал", "url": "https://t.me/uzri_sebya"}
                    ],
                    [
                        {"text": "✅ Проверить подписку", "callback_data": "check_subscription"}
                    ]
                ]
            }
            
            await send_telegram_message(
                chat_id,
                "🔒 Для использования поиска необходимо подписаться на канал!\n\n"
                "📢 Подпишитесь на @uzri_sebya и нажмите 'Проверить подписку'",
                reply_markup=keyboard
            )
            return

    # Check attempts
    if user.attempts_remaining <= 0 and not user.is_admin:
        await send_telegram_message(
            chat_id,
            "❌ У вас закончились попытки поиска!\n\n"
            "🔗 Пригласите друзей по реферальной ссылке:\n"
            "Используйте /referral для получения ссылки."
        )
        return

    # Detect search type
    search_type = detect_search_type(query)
    
    # Send searching message with detected type
    type_emojis = {
        "phone": "📱",
        "email": "📧", 
        "name": "👤",
        "car_number": "🚗",
        "username": "🆔",
        "ip_address": "🌐",
        "address": "🏠",
        "general": "🔍"
    }
    
    search_emoji = type_emojis.get(search_type, "🔍")
    await send_telegram_message(
        chat_id, 
        f"{search_emoji} Выполняю поиск...\n"
        f"🔍 Тип: {search_type}\n"
        f"⏱️ Подождите немного..."
    )

    try:
        # Call usersbox API
        headers = {"Authorization": USERSBOX_TOKEN}
        response = requests.get(
            f"{USERSBOX_BASE_URL}/search",
            headers=headers,
            params={"q": query},
            timeout=30
        )

        results = response.json()

        # Format and send results
        formatted_results = format_search_results(results, query, search_type)
        await send_telegram_message(chat_id, formatted_results)

        # Save search record
        search = Search(
            user_id=user.telegram_id,
            query=query,
            search_type=search_type,
            results=results,
            success=response.status_code == 200
        )
        await db.searches.insert_one(search.dict())

        # Deduct attempt (except for admin)
        if not user.is_admin and response.status_code == 200:
            await db.users.update_one(
                {"telegram_id": user.telegram_id},
                {"$inc": {"attempts_remaining": -1}}
            )
            
            # Update user object
            user.attempts_remaining -= 1

            # Show remaining attempts
            if user.attempts_remaining > 0:
                await send_telegram_message(
                    chat_id,
                    f"💎 Осталось попыток: {user.attempts_remaining}"
                )
            else:
                await send_telegram_message(
                    chat_id,
                    "❌ Попытки закончились!\n\n"
                    "🔗 Получите больше попыток, пригласив друзей:\n"
                    "Используйте /referral"
                )

    except requests.exceptions.RequestException as e:
        logging.error(f"Usersbox API error: {e}")
        await send_telegram_message(
            chat_id,
            "❌ Ошибка при выполнении поиска\n\n"
            "Сервис временно недоступен. Попробуйте позже."
        )
    except Exception as e:
        logging.error(f"Search error: {e}")
        await send_telegram_message(
            chat_id,
            "❌ Произошла ошибка при поиске\n\n"
            "Попробуйте еще раз или обратитесь к администратору."
        )

async def handle_balance_command(chat_id: int, user: User):
    """Handle balance command with enhanced statistics"""
    # Get user's search history
    recent_searches = await db.searches.find({"user_id": user.telegram_id}).sort("timestamp", -1).limit(5).to_list(5)
    total_searches = await db.searches.count_documents({"user_id": user.telegram_id})
    successful_searches = await db.searches.count_documents({"user_id": user.telegram_id, "success": True})

    balance_text = "💰 *═══════════════════════════*\n"
    balance_text += " 💎 *ВАШ БАЛАНС И СТАТИСТИКА*\n"
    balance_text += "*═══════════════════════════* 💰\n\n"

    # Balance section
    balance_text += "💎 *═══ БАЛАНС ПОПЫТОК ═══*\n"
    balance_text += f"🔍 *Доступно поисков:* `{user.attempts_remaining}`\n"
    balance_text += f"👥 *Приглашено друзей:* `{user.total_referrals}`\n"
    balance_text += f"📅 *Регистрация:* `{user.created_at.strftime('%d.%m.%Y %H:%M')}`\n"
    balance_text += f"⏰ *Последняя активность:* `{user.last_active.strftime('%d.%m.%Y %H:%M')}`\n\n"

    # Statistics section
    balance_text += "📊 *═══ СТАТИСТИКА ПОИСКОВ ═══*\n"
    balance_text += f"🔍 *Всего поисков:* `{total_searches}`\n"
    balance_text += f"✅ *Успешных:* `{successful_searches}`\n"
    
    if total_searches > 0:
        success_rate = (successful_searches / total_searches) * 100
        balance_text += f"📈 *Успешность:* `{success_rate:.1f}%`\n"
    else:
        balance_text += f"📈 *Успешность:* `0%`\n"
    
    balance_text += f"🎯 *Реферальный код:* `{user.referral_code}`\n\n"

    # Recent searches with types
    if recent_searches:
        balance_text += "🕐 *═══ ПОСЛЕДНИЕ ПОИСКИ ═══*\n"
        for i, search in enumerate(recent_searches[:3], 1):
            status = "✅" if search.get('success', False) else "❌"
            query = search.get('query', 'N/A')[:20] + "..." if len(search.get('query', '')) > 20 else search.get('query', 'N/A')
            search_type = search.get('search_type', 'general')
            date = search.get('timestamp', datetime.utcnow()).strftime('%d.%m %H:%M')
            
            type_emojis = {
                "phone": "📱", "email": "📧", "name": "👤", "car_number": "🚗",
                "username": "🆔", "ip_address": "🌐", "address": "🏠", "general": "🔍"
            }
            type_emoji = type_emojis.get(search_type, "🔍")
            
            balance_text += f"{status} {type_emoji} `{query}` - {date}\n"
        
        balance_text += "\n"

    # Recommendations based on attempts
    if user.attempts_remaining == 0:
        balance_text += "🚨 *═══ ПОПЫТКИ ЗАКОНЧИЛИСЬ ═══*\n"
        balance_text += "🔗 *Получите больше попыток:*\n"
        balance_text += "• Пригласите друзей по реферальной ссылке\n"
        balance_text += "• Используйте `/referral` для получения ссылки\n"
        balance_text += "• За каждого друга: +1 попытка\n\n"
    elif user.attempts_remaining <= 3:
        balance_text += "⚠️ *═══ МАЛО ПОПЫТОК ═══*\n"
        balance_text += "💡 Рекомендуем пригласить друзей для получения дополнительных попыток!\n"
        balance_text += "🔗 Команда: `/referral`\n\n"

    balance_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    balance_text += "💡 *Хотите больше попыток? Используйте* `/referral`"

    await send_telegram_message(chat_id, balance_text)

async def handle_referral_command(chat_id: int, user: User):
    """Handle referral command with enhanced referral system"""
    referral_link = f"https://t.me/{BOT_USERNAME}?start={user.referral_code}"

    # Get referral statistics
    referrals = await db.referrals.find({"referrer_id": user.telegram_id}).to_list(100)
    total_earned = len(referrals)

    referral_text = "🔗 *═══════════════════════════*\n"
    referral_text += " 💰 *РЕФЕРАЛЬНАЯ ПРОГРАММА*\n"
    referral_text += "*═══════════════════════════* 🔗\n\n"

    referral_text += "🎯 *═══ ВАША ССЫЛКА ═══*\n"
    referral_text += f"🔗 `{referral_link}`\n\n"
    referral_text += "📋 *Нажмите на ссылку выше для копирования*\n\n"

    referral_text += "📊 *═══ ВАША СТАТИСТИКА ═══*\n"
    referral_text += f"👥 *Приглашено друзей:* `{user.total_referrals}`\n"
    referral_text += f"💎 *Заработано попыток:* `{total_earned}`\n"
    referral_text += f"🎯 *Ваш код:* `{user.referral_code}`\n\n"

    referral_text += "💰 *═══ КАК ЭТО РАБОТАЕТ ═══*\n"
    referral_text += "1️⃣ *Поделитесь* ссылкой с друзьями\n"
    referral_text += "2️⃣ *Друг переходит* по вашей ссылке\n"
    referral_text += "3️⃣ *Друг регистрируется* в боте\n"
    referral_text += "4️⃣ *Вы получаете* +1 попытку поиска\n"
    referral_text += "5️⃣ *Друг также получает* +1 попытку\n"
    referral_text += "6️⃣ *Повторяйте* для неограниченных попыток!\n\n"

    referral_text += "🎁 *═══ БОНУСЫ ═══*\n"
    referral_text += "• 💎 За каждого друга: +1 попытка ВАМ\n"
    referral_text += "• 🎁 Друг также получает: +1 попытка\n"
    referral_text += "• 🔄 Попытки накапливаются навсегда\n"
    referral_text += "• 🚀 Неограниченное количество рефералов\n\n"

    referral_text += "📱 *═══ ГДЕ ПОДЕЛИТЬСЯ ═══*\n"
    referral_text += "• 💬 В мессенджерах (WhatsApp, Viber)\n"
    referral_text += "• 📱 В социальных сетях (VK, Instagram)\n"
    referral_text += "• 👨‍👩‍👧‍👦 С семьей и друзьями\n"
    referral_text += "• 💼 С коллегами по работе\n"
    referral_text += "• 🎮 В игровых чатах\n\n"

    # Status based on referrals
    if user.total_referrals >= 10:
        referral_text += "🏆 *═══ СТАТУС VIP ═══*\n"
        referral_text += "🌟 Поздравляем! Вы VIP-реферер!\n"
        referral_text += f"👑 {user.total_referrals} приглашенных друзей\n\n"
    elif user.total_referrals >= 5:
        referral_text += "🥇 *═══ СТАТУС МАСТЕР ═══*\n"
        referral_text += "⭐ Отличная работа! Вы мастер рефералов!\n"
        referral_text += f"🏅 {user.total_referrals} приглашенных друзей\n\n"
    elif user.total_referrals >= 1:
        referral_text += "🥉 *═══ ПЕРВЫЕ УСПЕХИ ═══*\n"
        referral_text += "👍 Хорошее начало!\n"
        referral_text += f"📈 {user.total_referrals} приглашенных друзей\n\n"

    referral_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    referral_text += "💡 *Чем больше друзей, тем больше поисков!*"

    await send_telegram_message(chat_id, referral_text)

async def handle_help_command(chat_id: int, user: User):
    """Handle help command with comprehensive guide"""
    help_text = "📖 *═══════════════════════════*\n"
    help_text += " 📚 *ПОДРОБНАЯ СПРАВКА*\n"
    help_text += "*═══════════════════════════* 📖\n\n"

    help_text += "🎯 *═══ ОСНОВНЫЕ КОМАНДЫ ═══*\n"
    help_text += "🔍 `/search [запрос]` - поиск по базам данных\n"
    help_text += "💰 `/balance` - баланс попыток и статистика\n"
    help_text += "🔗 `/referral` - реферальная ссылка\n"
    help_text += "🎯 `/capabilities` - все возможности поиска\n"
    help_text += "📖 `/help` - эта справка\n\n"

    help_text += "🔍 *═══ БЫСТРЫЕ ПРИМЕРЫ ═══*\n"
    help_text += "📱 *Телефон:* `+79123456789`\n"
    help_text += "📧 *Email:* `ivan@mail.ru`\n"
    help_text += "👤 *ФИО:* `Иван Петров`\n"
    help_text += "🚗 *Авто:* `А123ВС777`\n"
    help_text += "🆔 *Никнейм:* `@username`\n"
    help_text += "🏠 *Адрес:* `Москва Тверская 1`\n\n"

    help_text += "📊 *═══ ЧТО НАЙДЕТ БОТ ═══*\n"
    help_text += "• 📞 Данные по номерам телефонов\n"
    help_text += "• 📧 Информация по email адресам\n"
    help_text += "• 👥 Профили в социальных сетях\n"
    help_text += "• 🏠 Адресные данные и геолокация\n"
    help_text += "• 🚗 Информация по автомобилям\n"
    help_text += "• 💳 Банковские и платежные данные\n"
    help_text += "• 🛒 Данные интернет-магазинов\n"
    help_text += "• 📋 Государственные базы данных\n"
    help_text += "• 🎯 И многое другое из 100+ источников\n\n"

    help_text += "💎 *═══ СИСТЕМА ПОПЫТОК ═══*\n"
    help_text += "🎁 *При регистрации:* 0 попыток\n"
    help_text += "🔗 *За реферала:* +1 попытка вам и другу\n"
    help_text += "👥 *Безлимит:* приглашайте друзей\n"
    help_text += "⚡ *Админы:* неограниченные попытки\n\n"

    help_text += "🔗 *═══ РЕФЕРАЛЬНАЯ СИСТЕМА ═══*\n"
    help_text += "1️⃣ Получите ссылку: `/referral`\n"
    help_text += "2️⃣ Поделитесь с друзьями\n"
    help_text += "3️⃣ Друг переходит и регистрируется\n"
    help_text += "4️⃣ Оба получаете по +1 попытке\n"
    help_text += "5️⃣ Повторяйте для неограниченных попыток!\n\n"

    help_text += "⚠️ *═══ ВАЖНЫЕ ПРАВИЛА ═══*\n"
    help_text += "• 🚫 Не используйте для незаконных целей\n"
    help_text += "• 👮 Соблюдайте законы вашей страны\n"
    help_text += "• 🤝 Уважайте приватность других людей\n"
    help_text += "• 🔒 Не передавайте данные третьим лицам\n"
    help_text += "• 📢 Обязательна подписка на канал @uzri_sebya\n\n"

    help_text += "❓ *═══ ПРОБЛЕМЫ? ═══*\n"
    help_text += "📝 Напишите администратору: @eriksson_sop\n"
    help_text += "🔧 Или используйте команды для диагностики\n\n"

    help_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    help_text += "🚀 *Готов найти любую информацию!*"

    await send_telegram_message(chat_id, help_text)

async def handle_admin_command(chat_id: int, text: str, user: User):
    """Handle admin commands with enhanced statistics"""
    # Get system statistics
    total_users = await db.users.count_documents({})
    total_searches = await db.searches.count_documents({})
    total_referrals = await db.referrals.count_documents({})
    successful_searches = await db.searches.count_documents({"success": True})

    # Recent activity (last 24 hours)
    from datetime import datetime, timedelta
    yesterday = datetime.utcnow() - timedelta(days=1)
    recent_users = await db.users.count_documents({"created_at": {"$gte": yesterday}})
    recent_searches = await db.searches.count_documents({"timestamp": {"$gte": yesterday}})

    # Top users by referrals
    top_referrers = await db.users.find().sort("total_referrals", -1).limit(5).to_list(5)

    admin_text = "👑 *═══════════════════════════*\n"
    admin_text += " 🔧 *АДМИН ПАНЕЛЬ*\n"
    admin_text += "*═══════════════════════════* 👑\n\n"

    admin_text += "📊 *═══ ОБЩАЯ СТАТИСТИКА ═══*\n"
    admin_text += f"👥 *Всего пользователей:* `{total_users}`\n"
    admin_text += f"🔍 *Всего поисков:* `{total_searches}`\n"
    admin_text += f"✅ *Успешных поисков:* `{successful_searches}`\n"
    admin_text += f"🔗 *Всего рефералов:* `{total_referrals}`\n"
    
    if total_searches > 0:
        success_rate = (successful_searches / total_searches) * 100
        admin_text += f"📈 *Успешность:* `{success_rate:.1f}%`\n"
    
    admin_text += "\n"

    admin_text += "📈 *═══ АКТИВНОСТЬ (24ч) ═══*\n"
    admin_text += f"🆕 *Новых пользователей:* `{recent_users}`\n"
    admin_text += f"🔍 *Поисков за день:* `{recent_searches}`\n\n"

    admin_text += "🏆 *═══ ТОП РЕФЕРЕРЫ ═══*\n"
    for i, referrer in enumerate(top_referrers[:3], 1):
        name = referrer.get('first_name', 'Неизвестно')[:15]
        refs = referrer.get('total_referrals', 0)
        admin_text += f"{i}. `{name}` - {refs} рефералов\n"
    
    admin_text += "\n"

    admin_text += "🔧 *═══ АДМИН КОМАНДЫ ═══*\n"
    admin_text += "💎 `/give [ID] [попытки]` - выдать попытки\n"
    admin_text += "📊 `/stats` - подробная статистика\n"
    admin_text += "🔧 Используйте API для управления через фронтенд\n\n"

    admin_text += "📋 *═══ ПОЛЕЗНЫЕ ID ═══*\n"
    admin_text += f"🤖 *Ваш ID:* `{user.telegram_id}`\n"
    admin_text += f"🎯 *Ваш код:* `{user.referral_code}`\n\n"

    admin_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    admin_text += "👑 *Полный контроль над системой*"

    await send_telegram_message(chat_id, admin_text)

async def handle_give_attempts_command(chat_id: int, text: str, user: User):
    """Handle give attempts admin command"""
    parts = text.split()
    if len(parts) != 3:
        await send_telegram_message(
            chat_id,
            "❌ *Неверный формат команды*\n\n"
            "*Использование:* `/give [user_id] [attempts]`\n"
            "*Пример:* `/give 123456789 5`"
        )
        return

    try:
        target_user_id = int(parts[1])
        attempts_to_give = int(parts[2])

        # Check if user exists
        target_user = await db.users.find_one({"telegram_id": target_user_id})
        if not target_user:
            await send_telegram_message(
                chat_id,
                f"❌ Пользователь с ID {target_user_id} не найден"
            )
            return

        # Give attempts
        await db.users.update_one(
            {"telegram_id": target_user_id},
            {"$inc": {"attempts_remaining": attempts_to_give}}
        )

        # Notify admin
        await send_telegram_message(
            chat_id,
            f"✅ Пользователю {target_user_id} выдано {attempts_to_give} попыток"
        )

        # Notify user
        await send_telegram_message(
            target_user_id,
            f"🎁 *Вам выданы попытки!*\n\n"
            f"💎 Получено попыток: {attempts_to_give}\n"
            f"Можете продолжать поиск!"
        )

    except ValueError:
        await send_telegram_message(
            chat_id,
            "❌ Неверный формат ID пользователя или количества попыток"
        )
    except Exception as e:
        logging.error(f"Give attempts error: {e}")
        await send_telegram_message(
            chat_id,
            "❌ Ошибка при выдаче попыток"
        )

async def handle_stats_command(chat_id: int, user: User):
    """Handle stats admin command"""
    try:
        # Get comprehensive statistics
        total_users = await db.users.count_documents({})
        total_searches = await db.searches.count_documents({})
        total_referrals = await db.referrals.count_documents({})
        successful_searches = await db.searches.count_documents({"success": True})

        # Recent activity
        recent_users = await db.users.count_documents({
            "created_at": {"$gte": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)}
        })
        recent_searches = await db.searches.count_documents({
            "timestamp": {"$gte": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)}
        })

        # Search type distribution
        search_types = await db.searches.aggregate([
            {"$group": {"_id": "$search_type", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]).to_list(10)

        stats_text = "📊 *═══ ДЕТАЛЬНАЯ СТАТИСТИКА ═══*\n\n"
        
        stats_text += f"👥 *Всего пользователей:* {total_users}\n"
        stats_text += f"🔍 *Всего поисков:* {total_searches}\n"
        stats_text += f"✅ *Успешных поисков:* {successful_searches}\n"
        stats_text += f"🔗 *Рефералов:* {total_referrals}\n\n"

        stats_text += f"📈 *За сегодня:*\n"
        stats_text += f"• Новых пользователей: {recent_users}\n"
        stats_text += f"• Поисков: {recent_searches}\n\n"

        if total_searches > 0:
            success_rate = (successful_searches / total_searches) * 100
            stats_text += f"📊 *Успешность поисков:* {success_rate:.1f}%\n\n"

        if search_types:
            stats_text += "🔍 *Популярные типы поиска:*\n"
            for search_type in search_types[:5]:
                stats_text += f"• {search_type['_id']}: {search_type['count']}\n"

        await send_telegram_message(chat_id, stats_text)

    except Exception as e:
        logging.error(f"Stats error: {e}")
        await send_telegram_message(
            chat_id,
            "❌ Ошибка при получении статистики"
        )

# API endpoints for web dashboard
@api_router.post("/search")
async def api_search(query: str = Query(...)):
    """Search via usersbox API"""
    headers = {"Authorization": USERSBOX_TOKEN}
    try:
        response = requests.get(
            f"{USERSBOX_BASE_URL}/search",
            headers=headers,
            params={"q": query},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"API request failed: {str(e)}")

@api_router.get("/users")
async def get_users():
    """Get all users for admin dashboard"""
    users = await db.users.find().to_list(1000)
    for user in users:
        user["_id"] = str(user["_id"])
    return users

@api_router.get("/searches")
async def get_searches():
    """Get search history"""
    searches = await db.searches.find().sort("timestamp", -1).limit(100).to_list(100)
    for search in searches:
        search["_id"] = str(search["_id"])
    return searches

@api_router.post("/give-attempts")
async def give_attempts_api(user_id: int, attempts: int):
    """Give attempts to user via API"""
    try:
        result = await db.users.update_one(
            {"telegram_id": user_id},
            {"$inc": {"attempts_remaining": attempts}}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        # Notify user
        await send_telegram_message(
            user_id,
            f"🎁 *Вам выданы попытки!*\n\n"
            f"💎 Получено попыток: {attempts}\n"
            f"Можете продолжать поиск!"
        )

        return {"status": "success", "message": f"Gave {attempts} attempts to user {user_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/stats")
async def get_stats():
    """Get bot statistics"""
    try:
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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