# 💰 РУКОВОДСТВО ПО ИНТЕГРАЦИИ ПЛАТЕЖЕЙ

## 🤖 ИНТЕГРАЦИЯ С КРИПТОБОТОМ (@CryptoBot)

### 1. Подготовка:
```bash
# Установить дополнительные библиотеки
pip install aiohttp cryptobot-api-client
```

### 2. Получение API ключа:
1. Напишите @CryptoBot
2. Создайте приложение: `/newapp`
3. Получите API токен
4. Добавьте в `/app/backend/.env`:
```env
CRYPTOBOT_TOKEN="ваш_токен_здесь"
```

### 3. Код интеграции (добавить в server.py):
```python
from cryptobot import Client

async def create_crypto_invoice(user_id: int, amount: float):
    """Создать инвойс в криптоботе"""
    client = Client(os.environ['CRYPTOBOT_TOKEN'])
    
    invoice = await client.create_invoice(
        amount=amount,
        description=f"Пополнение УЗРИ - {amount}₽",
        payload=f"user_{user_id}_{amount}",
        currency_type="fiat",  # RUB
        fiat="RUB"
    )
    
    return invoice.pay_url, invoice.invoice_id

async def check_crypto_payment(invoice_id: str):
    """Проверить статус платежа"""
    client = Client(os.environ['CRYPTOBOT_TOKEN'])
    invoice = await client.get_invoices(invoice_ids=[invoice_id])
    return invoice[0].status == "paid"
```

### 4. Webhook для криптобота:
```python
@api_router.post("/crypto-webhook")
async def crypto_webhook(request: Request):
    """Webhook от криптобота"""
    data = await request.json()
    
    if data.get('status') == 'paid':
        payload = data.get('payload', '')
        if payload.startswith('user_'):
            parts = payload.split('_')
            user_id = int(parts[1])
            amount = float(parts[2])
            
            # Начислить баланс
            await db.users.update_one(
                {"telegram_id": user_id},
                {"$inc": {"balance": amount}}
            )
            
            # Уведомить пользователя
            await send_telegram_message(
                user_id,
                f"✅ Пополнение успешно!\n💰 Зачислено: {amount} ₽"
            )
    
    return {"status": "ok"}
```

## ⭐ ИНТЕГРАЦИЯ СО ЗВЕЗДАМИ TELEGRAM

### 1. Настройка бота:
1. Обратитесь к @BotFather
2. Выберите своего бота
3. Включите платежи: `/mybots -> Bot Settings -> Payments`

### 2. Код интеграции:
```python
async def create_star_invoice(chat_id: int, amount: int):
    """Создать инвойс в звездах (amount в звездах)"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createInvoiceLink"
    
    payload = {
        "title": "Пополнение баланса УЗРИ",
        "description": f"Пополнение на {amount * 2} рублей",
        "payload": f"stars_{chat_id}_{amount}",
        "currency": "XTR",  # Telegram Stars
        "prices": [{"label": "Пополнение", "amount": amount}]
    }
    
    response = requests.post(url, json=payload)
    return response.json().get('result', '')

# В handle_payment_callback добавить:
elif data == "pay_stars":
    stars_needed = 50  # 50 звезд = 100 рублей
    invoice_link = await create_star_invoice(chat_id, stars_needed)
    
    await send_telegram_message(
        chat_id,
        f"⭐ **ОПЛАТА ЗВЕЗДАМИ**\n\n💎 Стоимость: {stars_needed} звезд\n💰 Получите: 100 ₽\n\n[Оплатить]({invoice_link})",
        reply_markup=create_back_keyboard()
    )
```

### 3. Обработка платежей звездами:
```python
# В handle_telegram_update добавить:
pre_checkout_query = update_data.get('pre_checkout_query')
if pre_checkout_query:
    # Подтвердить оплату
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerPreCheckoutQuery"
    requests.post(url, json={
        "pre_checkout_query_id": pre_checkout_query['id'],
        "ok": True
    })

successful_payment = message.get('successful_payment')
if successful_payment:
    payload = successful_payment.get('invoice_payload', '')
    if payload.startswith('stars_'):
        parts = payload.split('_')
        user_id = int(parts[1])
        stars = int(parts[2])
        rub_amount = stars * 2  # 1 звезда = 2 рубля
        
        # Начислить баланс
        await db.users.update_one(
            {"telegram_id": user_id},
            {"$inc": {"balance": rub_amount}}
        )
        
        await send_telegram_message(
            chat_id,
            f"✅ Оплата звездами успешна!\n⭐ Потрачено: {stars} звезд\n💰 Зачислено: {rub_amount} ₽"
        )
```

## 📊 ГОТОВЫЕ КНОПКИ ПОПОЛНЕНИЯ

### Обновить handle_payment_callback:
```python
async def handle_payment_callback(chat_id: int, user: User, data: str):
    if data == "pay_crypto":
        # Варианты пополнения через криптобот
        amounts = [100, 250, 500, 1000, 2500]
        keyboard = []
        
        for amount in amounts:
            keyboard.append([{
                "text": f"💰 {amount} ₽", 
                "callback_data": f"crypto_{amount}"
            }])
        
        keyboard.append([{"text": "◀️ Назад", "callback_data": "menu_balance"}])
        
        await send_telegram_message(
            chat_id,
            "🤖 **ПОПОЛНЕНИЕ ЧЕРЕЗ КРИПТОБОТ**\n\nВыберите сумму:",
            reply_markup={"inline_keyboard": keyboard}
        )
    
    elif data.startswith("crypto_"):
        amount = int(data.split("_")[1])
        pay_url, invoice_id = await create_crypto_invoice(user.telegram_id, amount)
        
        await send_telegram_message(
            chat_id,
            f"🤖 **КРИПТОБОТ ПЛАТЕЖ**\n\n💰 Сумма: {amount} ₽\n\n[Оплатить]({pay_url})",
            reply_markup=create_back_keyboard()
        )
```

## 🔧 ФИНАЛЬНЫЕ ШАГИ:

1. **Добавьте токены в .env:**
```env
CRYPTOBOT_TOKEN="ваш_токен_криптобота"
```

2. **Установите библиотеки:**
```bash
pip install aiohttp cryptobot-api-client
```

3. **Настройте webhook криптобота:**
- URL: `https://ваш_домен.com/api/crypto-webhook`

4. **Включите платежи в BotFather для звезд**

5. **Протестируйте платежи**

## 💡 ДОПОЛНИТЕЛЬНЫЕ ВОЗМОЖНОСТИ:

- **Автоматическое определение курса валют**
- **Промокоды и скидки**  
- **История платежей в профиле**
- **Уведомления об успешных платежах**
- **Возврат средств (по запросу)**

Система готова к интеграции! 🚀