#!/usr/bin/env python3
"""
Telegram Bot Polling Script
Получает сообщения через getUpdates и передает их в webhook API
"""

import requests
import time
import json
import logging
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv('/app/backend/.env')

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
WEBHOOK_URL = f"http://localhost:8001/api/webhook/{os.environ['WEBHOOK_SECRET']}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_updates(offset=None):
    """Get updates from Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    
    try:
        response = requests.get(url, params=params, timeout=35)
        return response.json()
    except Exception as e:
        logger.error(f"Error getting updates: {e}")
        return None

def process_update(update):
    """Send update to webhook"""
    try:
        response = requests.post(WEBHOOK_URL, json=update, timeout=10)
        if response.status_code == 200:
            logger.info(f"Processed update {update['update_id']}")
        else:
            logger.error(f"Webhook error: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Error processing update: {e}")

def main():
    """Main polling loop"""
    logger.info("Starting Telegram bot polling...")
    logger.info(f"Webhook URL: {WEBHOOK_URL}")
    offset = None
    
    while True:
        try:
            updates_response = get_updates(offset)
            if not updates_response or not updates_response.get('ok'):
                logger.error("Failed to get updates")
                time.sleep(5)
                continue
            
            updates = updates_response.get('result', [])
            
            for update in updates:
                process_update(update)
                offset = update['update_id'] + 1
            
            if not updates:
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Bot stopped")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()