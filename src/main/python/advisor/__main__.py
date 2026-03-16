import logging
import os
import sys
import signal

from advisor.MA_DynamAdvisor import Main

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("advisor_engine.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("MAIN")

def main():
    bot = Main()
    _register_signal_handlers(bot.shutdown)
    bot.start()

def _register_signal_handlers(shutdown_func):
    signal.signal(signal.SIGINT, shutdown_func)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown_func)

def ensure_single_instance(lock_file):
    if os.path.exists(lock_file):
        logger.warning("Another instance of MA_DynamAdvisor is already running.")
        return False
    with open(lock_file, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


if __name__ == "__main__":
    lock_file = os.path.splitext(os.path.basename(sys.argv[0]))[0] + ".lock"

    try:
        if not ensure_single_instance(lock_file):
            raise RuntimeError("Another instance is running")
        logger.log(level=1, msg="Running bot module")
        _register_signal_handlers(Main)
        bot = main()
    except KeyboardInterrupt:
        logger.info("Bot stopped manually.")
    except Exception as e:
        logger.exception("Processes stopped with: %s", e)
    except RuntimeError as e:
        logger.exception("Processes stopped with: %s", e)
    finally:
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                sys.exit(1)
            except Exception as e:
                logger.warning("Could not remove lock file: %s", e)
