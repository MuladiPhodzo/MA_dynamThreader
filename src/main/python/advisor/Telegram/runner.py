import sys
import signal
import threading
from .core import TelegramMessenger
from .utils.singleton import check_and_create_lock, cleanup_lock
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


async def run() -> TelegramMessenger | None:
    """Main Telegram bot runner with graceful startup and shutdown."""
    if not check_and_create_lock():
        logger.info("🟡 Existing Telegram bot instance detected. Exiting.")
        return

    logger.info("🚀 Launching Telegram bot runner...")

    bot = TelegramMessenger()

    # --- Stop callback (called on /stop command)
    def stop_trading_bot():
        logger.info("🧩 Callback: Stopping trading bot...")
        # Example: Here you can safely terminate a trading process or background thread
        # Example:
        # trading_thread.stop()
        # subprocess.Popen(['taskkill', '/F', '/IM', 'trade_bot.exe'])
        pass

    # --- Graceful shutdown handler (works on both Windows and Linux)
    shutting_down = threading.Event()

    def shutdown():
        if shutting_down.is_set():
            return  # prevent multiple shutdowns
        shutting_down.set()
        logger.info("🛑 System signal received — shutting down Telegram bot...")
        try:
            bot.stop_bot()
        finally:
            cleanup_lock()
            logger.info("✅ Cleanup complete.")
            sys.exit(0)

    # --- Run Telegram bot
    try:
        bot.start_bot()
    except Exception as e:
        logger.info(f"❌ Telegram runner crashed: {e}")
        shutdown()
    finally:
        cleanup_lock()
