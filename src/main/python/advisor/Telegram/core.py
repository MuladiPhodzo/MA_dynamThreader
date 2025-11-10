import asyncio
import requests
import sys, os
import signal
import threading
import json
from pathlib import Path
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update
from .utils.env_loader import load_env


class TelegramMessenger:
    """Robust, restartable Telegram bot with async-safe control and persistent chat ID."""

    CHAT_ID_FILE = Path("telegram_chat.json")

    def __init__(self, chat_id=None):
        self.BOT_TOKEN = load_env()
        if not self.BOT_TOKEN:
            raise ValueError("❌ TELEGRAM_BOT_TOKEN missing in .env")

        self.chat_id = chat_id or self._load_chat_id()
        self.app: Application | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.stop_callback = None
        self.should_run = False

    # -------------------------------------------------------------------------
    # Chat ID Persistence
    # -------------------------------------------------------------------------
    def _load_chat_id(self):
        if self.CHAT_ID_FILE.exists():
            try:
                data = json.loads(self.CHAT_ID_FILE.read_text())
                chat_id = data.get("chat_id")
                if chat_id:
                    print(f"💾 Restored chat ID: {chat_id}")
                    return chat_id
            except Exception as e:
                print(f"⚠️ Failed to load chat ID: {e}")
        return None

    def _save_chat_id(self, chat_id):
        try:
            self.CHAT_ID_FILE.write_text(json.dumps({"chat_id": chat_id}))
            print(f"💾 Saved chat ID: {chat_id}")
        except Exception as e:
            print(f"⚠️ Failed to save chat ID: {e}")

    def _delete_chat_id(self):
        if self.CHAT_ID_FILE.exists():
            try:
                self.CHAT_ID_FILE.unlink()
                print("🧹 Removed saved chat ID.")
            except Exception as e:
                print(f"⚠️ Failed to delete chat ID: {e}")

    # -------------------------------------------------------------------------
    # External callback
    # -------------------------------------------------------------------------
    def set_callback(self, callback):
        """Set external callback to run when /stop is triggered."""
        self.stop_callback = callback

    # -------------------------------------------------------------------------
    # Telegram command handlers
    # -------------------------------------------------------------------------
    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = update.effective_chat.id
        self._save_chat_id(self.chat_id)

        if self.should_run:
            await update.message.reply_text("♻️ Already running — restarting bot...")
            self.restart_bot()
            return

        self.should_run = True
        await update.message.reply_text(f"✅ Advisor started.\nChat ID: {self.chat_id}")

    async def _stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.should_run = False
        await update.message.reply_text("🛑 Advisor stopped by user.")

        if self.stop_callback:
            try:
                if asyncio.iscoroutinefunction(self.stop_callback):
                    await self.stop_callback()
                else:
                    self.stop_callback()
            except Exception as e:
                print(f"⚠️ Stop callback failed: {e}")

        await self.stop_async()

    async def _status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id or self.chat_id
        if not chat_id:
            await update.message.reply_text("❌ Chat ID not set. Use /start first.")
            return

        info = self.get_account_info()
        await context.bot.send_message(chat_id=chat_id, text=f"📊 Account Status:\n{info}")

    # -------------------------------------------------------------------------
    # Account Info
    # -------------------------------------------------------------------------
    def get_account_info(self):
        try:
            import advisor.Client.mt5Client as Client
            return Client.MetaTrader5Client.account_info
        except Exception as e:
            return f"⚠️ Error retrieving account info: {e}"

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------
    async def _initialize_bot(self):
        if self.app:
            print("⚙️ Bot already initialized.")
            return

        self.app = Application.builder().token(self.BOT_TOKEN).build()
        self.app.add_handler(CommandHandler("start", self._start))
        self.app.add_handler(CommandHandler("stop", self._stop))
        self.app.add_handler(CommandHandler("status", self._status))
        print("🤖 Telegram bot initialized.")

    async def _shutdown(self):
        if not self.app:
            return

        print("🛑 Shutting down Telegram bot...")
        try:
            await self.app.stop()
            await self.app.shutdown()
        except Exception as e:
            print(f"⚠️ Error during shutdown: {e}")
        else:
            print("✅ Telegram bot stopped cleanly.")
        finally:
            self._delete_chat_id()
            self.app = None
            self.should_run = False

    async def _main(self):
        await self._initialize_bot()
        self.loop = asyncio.get_running_loop()

        # Cross-platform signal handling
        if sys.platform != "win32":
            def handle_signal():
                print("🛑 Signal received — stopping bot...")
                asyncio.create_task(self._shutdown())

            for sig in (signal.SIGINT, signal.SIGTERM):
                self.loop.add_signal_handler(sig, handle_signal)
        else:
            print("⚠️ Signal handling disabled on Windows.")

        print("🚀 Telegram bot running...")
        await self.app.run_polling(close_loop=False)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    async def start_async(self):
        return await asyncio.run(self._main())

    async def start_bot(self):
        if self.app:
            print("⚠️ Telegram bot already running.")
            return

        print("🚀 Starting Telegram bot...")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            print("⚙️ Running in background thread...")
            self.thread = threading.Thread(target=lambda: self.start_async(), daemon=True)
            self.thread.start()
        else:
           await self.start_async()

    async def stop_async(self):
        await self._shutdown()

    def stop_bot(self):
        if self.loop and self.app:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
        else:
            print("⚠️ Telegram bot not running or loop unavailable.")

    # -------------------------------------------------------------------------
    # Restart Handling
    # -------------------------------------------------------------------------
    def restart_bot(self):
        """Gracefully restart the bot with a clean event loop."""
        print("♻️ Restarting Telegram bot...")

        def run_new_loop():
            try:
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                new_loop.run_until_complete(self._main())
            except Exception as e:
                print(f"⚠️ Failed to restart bot: {e}")

        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
        threading.Thread(target=run_new_loop, daemon=True).start()

    # -------------------------------------------------------------------------
    # Message Sending
    # -------------------------------------------------------------------------
    async def send_message(self, message: str):
        """Send a message via bot API (if app is active)."""
        if self.app and self.chat_id:
            try:
                await self.app.bot.send_message(chat_id=self.chat_id, text=message, parse_mode="HTML")
                print("✅ Message sent successfully via bot.")
                return
            except Exception as e:
                print(f"⚠️ send_message failed via bot: {e}")

        # fallback (HTTP request)
        if not self.BOT_TOKEN or not self.chat_id:
            print("⚠️ Cannot send message: missing bot token or chat_id.")
            return

        try:
            url = f"https://api.telegram.org/bot{self.BOT_TOKEN}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}
            response = requests.post(url, data=payload)
            if response.status_code == 200:
                print("✅ Message sent via fallback.")
            else:
                print(f"❌ Failed to send: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"❌ Exception while sending message: {e}")
