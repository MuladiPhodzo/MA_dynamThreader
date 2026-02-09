import multiprocessing
from filelock import FileLock
import threading
# Global process-safe locks
PROCESS_LOCK = multiprocessing.Lock()
FILE_LOCK = FileLock()
SYMBOL_LOCK = threading.Lock()
THREAD_LOCK = threading.Lock()
