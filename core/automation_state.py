import threading

class AutomationControl:
    def __init__(self):
        self._lock = threading.Lock()
        self.state = "RUNNING"  # or "PAUSED", "STOPPED"
        self.mode = "RETRY"     # or "WAIT", "HOME"

    def set_state(self, state):
        with self._lock:
            self.state = state

    def get_state(self):
        with self._lock:
            return self.state

    def set_mode(self, mode):
        with self._lock:
            self.mode = mode

    def get_mode(self):
        with self._lock:
            return self.mode

AUTOMATION = AutomationControl()
