# utils/logger.py
from datetime import datetime

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{level} {timestamp}] {msg}"
    print(entry)
    with open("logs/actions.log", "a") as f:
        f.write(entry + "\n")


