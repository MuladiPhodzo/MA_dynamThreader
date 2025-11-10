import os, json, time, psutil

LOCK_FILE = "MA_DynamAdvisor.lock"

def check_and_create_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                data = json.load(f)
            pid = data.get("pid")
            if not psutil.pid_exists(pid):
                print("⚠️ Stale lock detected. Removing old instance.")
                os.remove(LOCK_FILE)
            else:
                print(f"⚠️ Bot already running (PID {pid}). Attaching to it or exiting.")
                return False
        except Exception:
            os.remove(LOCK_FILE)
    with open(LOCK_FILE, "w") as f:
        json.dump({"pid": os.getpid(), "timestamp": time.time()}, f)
    return True

def cleanup_lock():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)
