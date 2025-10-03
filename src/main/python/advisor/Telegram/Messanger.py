from dotenv import load_dotenv
import os, threading, asyncio
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update
import requests, sys

from pathlib import Path
from dotenv import load_dotenv
import os, sys

class TelegramMessenger:
    def __init__(self, chat_id=None):
        # Detect if running as exe (PyInstaller)
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys._MEIPASS)
        else:
            # Go up from /src/main/python/advisor/Telegram/Messanger.py to project root
            base_dir = Path(__file__).resolve().parents[5]

        env_path = base_dir / ".env"
        print(f"🔍 Loading .env from: {env_path}")  # Debug log

        load_dotenv(dotenv_path=env_path)

        self.BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.BOT_TOKEN:
            raise ValueError("❌ TELEGRAM_BOT_TOKEN not found in .env file")

           
        self.chat_id = chat_id
        self.should_run = True  # 🔁 Flag to control bot execution
        
    def run_bot_async(self):
        threading.Thread(target=self.run_bot, daemon=True).start()
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.chat_id = chat_id
        self.should_run = True
        await context.bot.send_message(chat_id=chat_id, text=f"✅ Advisor started.\nChat ID: {chat_id}")

    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.should_run = False
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🛑 Advisor stopped by user.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.should_run:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Advisor is running.")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="🛑 Advisor is stopped.")
            
    def run_bot(self):
        """
        Starts the Telegram bot and waits for commands.
        """
        asyncio.set_event_loop(asyncio.new_event_loop())  # 🧠 create and set event loop in thread
        loop = asyncio.get_event_loop()

        app = Application.builder().token(self.BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("stop", self.stop))  # 🆕 Add stop command
        app.add_handler(CommandHandler("status", self.status))  # 🆕 Add status command

        print("🤖 Bot is running. Use /start to begin and /stop to stop the advisor.")
        loop.run_until_complete(app.run_polling())  # 🧠 run polling in the new loop


    def send_message(self, message):
        if not self.chat_id:
            print("❌ Chat ID not set. Use /start on your bot first.")
            return

        url = f"https://api.telegram.org/bot{self.BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            response = requests.post(url, data=payload)
            if response.status_code == 200:
                print("✅ Message sent successfully")
            else:
                print(f"❌ Failed to send message: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"❌ Exception occurred while sending message: {e}")
