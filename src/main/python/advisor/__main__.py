import os
import sys
import signal
from pathlib import Path
import psutil

from advisor.MA_DynamAdvisor import Main
from advisor.utils.logging_setup import configure_logging, get_logger
from advisor.utils.error_handling import install_exception_hooks

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

configure_logging()
logger = get_logger("MAIN")
install_exception_hooks(logger)

def main():
    global bot
    try:
        bot = Main()
        _register_signal_handlers(bot.shutdown)
        bot.start()
    except RuntimeError as e:
        logger.exception("Error occurred while running the bot: %s", e)

def _register_signal_handlers(shutdown_func):
    signal.signal(signal.SIGINT, shutdown_func)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown_func)

def ensure_single_instance(lock_file):
    lock_path = Path(lock_file)
    if lock_path.exists():
        pid = None
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
        except Exception:
            pid = None

        if pid and psutil.pid_exists(pid):
            logger.warning("Another instance of MA_DynamAdvisor is already running.")
            return False

        try:
            lock_path.unlink()
            logger.warning("Removed stale lock file.")
        except Exception as e:
            logger.warning("Could not remove stale lock file: %s", e)
            return False

    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    return True


if __name__ == "__main__":
    lock_file = os.path.splitext(os.path.basename(sys.argv[0]))[0] + ".lock"
    exit_code = 0

    try:
        if not ensure_single_instance(lock_file):
            raise RuntimeError("Another instance is running")
        logger.log(level=1, msg="Running bot module")
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped manually.")
        exit_code = 0
    except RuntimeError as e:
        exit_code = 1
        logger.exception("Processes stopped with: %s", e)
    except Exception as e:
        exit_code = 1
        logger.exception("Processes stopped with: %s", e)
    finally:
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception as e:
                logger.warning("Could not remove lock file: %s", e)
        exc = sys.exc_info()[1]
        if isinstance(exc, SystemExit):
            code = exc.code
            if isinstance(code, int):
                exit_code = code
        sys.exit(exit_code)
