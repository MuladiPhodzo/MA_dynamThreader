import asyncio
import logging
import threading
import sys
from typing import Callable


def install_exception_hooks(logger: logging.Logger) -> None:
    """
    Install global exception hooks for uncaught exceptions in main and threads.
    """
    def _sys_hook(exc_type, exc, tb):
        logger.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def _thread_hook(args: threading.ExceptHookArgs):
        logger.critical("Uncaught thread exception in %s", args.thread.name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _sys_hook
    try:
        threading.excepthook = _thread_hook  # Python 3.8+
    except Exception:
        pass


def install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop, logger: logging.Logger) -> None:
    """
    Install an asyncio exception handler to capture background task failures.
    """
    def _handler(_: asyncio.AbstractEventLoop, context: dict):
        msg = context.get("message", "Unhandled asyncio exception")
        exc = context.get("exception")
        if exc:
            logger.critical(msg, exc_info=exc)
        else:
            logger.critical("%s | context=%s", msg, context)

    loop.set_exception_handler(_handler)


def guard(logger: logging.Logger, fallback: Callable | None = None):
    """
    Decorator-like wrapper to guard a callable and log exceptions.
    """
    def _wrap(fn):
        def _inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception("Unhandled error in %s", fn.__name__)
                if fallback:
                    return fallback()
                return None
        return _inner
    return _wrap
