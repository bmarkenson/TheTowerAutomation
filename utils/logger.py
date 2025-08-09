# utils/logger.py
from datetime import datetime
import os

def log(msg, level="INFO"):
    """
    Write a timestamped log entry to stdout and append to logs/actions.log.

    Args:
        msg (str): The log message text.
        level (str, optional): Log level label (e.g., "INFO", "ERROR"). Defaults to "INFO".

    Side effects:
        - Prints to stdout.
        - Creates logs/ directory if missing.
        - Appends entry to logs/actions.log.

    Raises:
        OSError: If unable to create logs/ directory or write to the log file.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{level} {timestamp}] {msg}"
    print(entry)

    os.makedirs("logs", exist_ok=True)
    with open("logs/actions.log", "a") as f:
        f.write(entry + "\n")
