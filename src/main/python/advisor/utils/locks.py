from multiprocessing import Lock
import threading

# Global process-safe locks
CACHE_LOCK = Lock()
FILE_LOCK = Lock()
SYMBOL_LOCK = Lock()
THREAD_LOCK = threading.Lock()
